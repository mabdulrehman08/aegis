"""Locked in-memory SIMULATION state. Never contacts devices or real dispatch.

Lock ordering: the store lock guards only the session table and is never
acquired while a session lock is held. Each session lock guards all of that
session's state. The solver always runs outside every lock; its result is
installed only if the scene revision and epoch are unchanged (compare-and-set).
Every returned object is a deep copy.

Time is part of the scene: the freshness classification (fresh/stale/future)
of every associated observation is a "freshness signature". Whenever a sweep
(any mutation, snapshot, tick, or replan) finds that it changed, the scene
revision bumps exactly as for any other impactful change. Proposals also expire
no later than the next freshness horizon, so an approval can never be dispatched
against observations that went stale after it was granted.
"""

import hashlib
import json
import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

from backend.app.models import (
    Assignment,
    Casualty,
    CVObservation,
    MedicalAssessment,
    Obstacle,
    OccupancyGrid,
    Position,
    Responder,
    Scenario,
    SceneMap,
)
from backend.app.services.scenarios import load_demo_scenario
from backend.app.simulation.events import EventLog
from backend.app.simulation.models import (
    ActionResponse,
    AssociationRequest,
    DecisionRequest,
    DispatchRequest,
    EventEnvelope,
    EventPage,
    RecommendationState,
    ReplanRequest,
    RequiredCapabilitiesRequest,
    ResetRequest,
    ResponderUpdateRequest,
    SimulatedAssignment,
    SimulatedDeviceEvent,
    SimulateDeviceRequest,
    SimulationApproval,
    SimulationDispatch,
    SimulationProposal,
    SimulationState,
    TrackRecord,
)

Clock = Callable[[], datetime]
Solver = Callable[[Any], Any]

DEMO_SCENARIO_ID = "demo"
DEMO_MAP_REVISION = "sim-demo-map-1"
OPEN_PROPOSAL_STATUSES = ("pending_approval", "approved")
MILESTONE_RANK = {"dispatched": 0, "delivered": 1, "acknowledged": 2, "completed": 3}
MAX_IDEMPOTENCY_KEYS = 4096
EXPLICIT_REPLAN_ATTEMPTS = 3


class SimulationError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _not_found(detail: str) -> SimulationError:
    return SimulationError(404, detail)


def _conflict(detail: str) -> SimulationError:
    return SimulationError(409, detail)


def _invalid(detail: str) -> SimulationError:
    return SimulationError(422, detail)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _fingerprint(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _map_fingerprint(scene_map: SceneMap) -> str:
    return _fingerprint(_dump(scene_map))


def _default_solver(request: Any) -> Any:
    from optimization.solver import optimize

    return optimize(request)


def demo_scene_map(frame_id: str) -> SceneMap:
    """Known synthetic 24x20 grid at 1 m: free except one wall at x=8..9, y=2..13."""
    width, height = 24, 20
    cells = [0] * (width * height)
    for row in range(2, 13):
        cells[row * width + 8] = 100

    def point(x: float, y: float) -> Position:
        return Position(frame_id=frame_id, x_m=x, y_m=y)

    return SceneMap(
        frame_id=frame_id, revision=DEMO_MAP_REVISION,
        obstacles=[Obstacle(id="sim-wall-a",
                            vertices=[point(8, 2), point(9, 2), point(9, 13), point(8, 13)])],
        occupancy_grid=OccupancyGrid(origin=point(0, 0), resolution_m=1.0,
                                     width=width, height=height, cells=cells),
    )


@dataclass
class _Track:
    track_id: str
    latest: CVObservation
    count: int = 1
    casualty_id: str | None = None


@dataclass
class _Idempotent:
    fingerprint: str
    response: ActionResponse | None  # None: reserved by an in-flight request


@dataclass
class _SolverJob:
    epoch: int
    revision: int
    trigger: str
    request: Any
    now: datetime


@dataclass
class _Session:
    scenario_id: str
    events: EventLog
    lock: threading.RLock = field(default_factory=threading.RLock)
    epoch: int = 0
    scene_revision: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    idempotency: "OrderedDict[str, _Idempotent]" = field(default_factory=OrderedDict)
    # Scene (reset per epoch).
    meta: Scenario | None = None
    casualties: dict[str, Casualty] = field(default_factory=dict)
    responders: dict[str, Responder] = field(default_factory=dict)
    scene_map: SceneMap | None = None
    required_capabilities: dict[str, list[str]] = field(default_factory=dict)
    tracks: dict[str, _Track] = field(default_factory=dict)
    casualty_tracks: dict[str, str] = field(default_factory=dict)
    observation_fingerprints: dict[str, str] = field(default_factory=dict)
    responder_reported_at: dict[str, datetime] = field(default_factory=dict)
    responder_preferences: dict[str, str] = field(default_factory=dict)
    resolved: set[str] = field(default_factory=set)
    proposals: dict[str, SimulationProposal] = field(default_factory=dict)
    proposal_context: dict[str, tuple[Scenario, Any]] = field(default_factory=dict)
    current_proposal_id: str | None = None
    approvals: dict[str, SimulationApproval] = field(default_factory=dict)
    dispatches: dict[str, SimulationDispatch] = field(default_factory=dict)
    assignments: dict[str, SimulatedAssignment] = field(default_factory=dict)
    device_events: dict[str, SimulatedDeviceEvent] = field(default_factory=dict)
    map_revisions: dict[str, str] = field(default_factory=dict)
    freshness_signature: tuple = ()
    last_recommendation: RecommendationState | None = None

    def next_id(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        return f"sim-{kind}-{self.counters[kind]:04d}"

    def emit(self, event_type: str, payload: dict[str, Any], now: datetime) -> None:
        self.events.append(event_type, payload, now)

    def active_assignments(self) -> list[SimulatedAssignment]:
        return [item for item in self.assignments.values() if item.status != "completed"]

    def scenario(self, *, include_resolved: bool = True) -> Scenario:
        assert self.meta is not None
        casualties = [casualty.model_copy(deep=True) for casualty in self.casualties.values()
                      if include_resolved or casualty.id not in self.resolved]
        return Scenario(
            schema_version="1.0", id=self.meta.id, name=self.meta.name, synthetic=True,
            frame_id=self.meta.frame_id, created_at=self.meta.created_at,
            casualties=casualties,
            responders=[responder.model_copy(deep=True) for responder in self.responders.values()],
            scene_map=self.scene_map.model_copy(deep=True) if self.scene_map else None,
        )


class SimulationStore:
    """Authoritative SIMULATION state for all scenarios in this process.

    ``clock`` and ``solver`` are injectable for deterministic tests. The solver
    receives an ``optimization.models.OptimizationRequest`` and must return an
    ``OptimizationResult``; only assignments for casualties/responders without
    active work are treated as new proposals (preserved work is never re-proposed).
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        solver: Solver | None = None,
        solver_name: str = "ortools",
        auto_recommend: bool = True,
        proposal_ttl_seconds: float = 120.0,
        freshness_seconds: float = 30.0,
        min_confidence: float = 0.5,
        clock_skew_seconds: float = 5.0,
        max_events: int = 1000,
    ):
        self.clock = clock or _utcnow
        self.solver = solver or _default_solver
        self.solver_name = solver_name
        self.auto_recommend = auto_recommend
        self.proposal_ttl = timedelta(seconds=proposal_ttl_seconds)
        self.freshness_seconds = freshness_seconds
        self.min_confidence = min_confidence
        self.skew = timedelta(seconds=clock_skew_seconds)
        self.max_events = max_events
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._nonce = secrets.token_hex(4)

    # --- Session lookup ---------------------------------------------------------

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise RuntimeError("Simulation clock must return timezone-aware datetimes")
        return now

    def _get_or_create(self, scenario_id: str) -> _Session:
        with self._lock:
            session = self._sessions.get(scenario_id)
            if session is None:
                session = _Session(scenario_id=scenario_id,
                                   events=EventLog(scenario_id, self._nonce, self.max_events))
                self._sessions[scenario_id] = session
            return session

    def _session(self, scenario_id: str) -> _Session:
        """Existing loaded session; ``demo`` is loaded lazily on first use."""
        with self._lock:
            session = self._sessions.get(scenario_id)
        if session is not None and session.meta is not None:
            return session
        if scenario_id != DEMO_SCENARIO_ID:
            raise _not_found(f"Simulation {scenario_id} is not loaded; POST its reset first")
        self.reset(scenario_id, ResetRequest(), only_if_unloaded=True)
        return self._get_or_create(scenario_id)

    # --- Reads ------------------------------------------------------------------

    def tick(self, scenario_id: str) -> None:
        """Apply time-driven transitions (expiry, observation freshness) and replan once."""
        session = self._session(scenario_id)
        with session.lock:
            now = self._now()
            job = None
            if self._sweep(session, now) and self.auto_recommend:
                job = self._prepare(session, now, "observation_freshness_changed")
        if job is not None:
            self._execute(session, job)

    def snapshot(self, scenario_id: str) -> SimulationState:
        self.tick(scenario_id)
        session = self._session(scenario_id)
        with session.lock:
            return self._state(session, self._now())

    def snapshot_envelope(self, scenario_id: str) -> EventEnvelope:
        """Resync message: not stored; ``sequence`` is the last included event."""
        state = self.snapshot(scenario_id)
        return EventEnvelope(
            event_id=f"sim-{scenario_id}-{self._nonce}-snapshot-{secrets.token_hex(6)}",
            type="simulation.snapshot", scenario_id=scenario_id,
            occurred_at=state.generated_at, sequence=max(state.sequence, 1),
            payload=_dump(state),
        )

    def events(self, scenario_id: str, after_sequence: int = 0, limit: int = 200) -> EventPage:
        session = self._session(scenario_id)
        with session.lock:
            events, gap = session.events.after(after_sequence, limit)
            return EventPage(scenario_id=scenario_id, events=events,
                             latest_sequence=session.events.sequence,
                             oldest_retained_sequence=session.events.oldest_sequence, gap=gap)

    def explanation(self, scenario_id: str, proposal_id: str) -> dict[str, Any]:
        session = self._session(scenario_id)
        with session.lock:
            context = session.proposal_context.get(proposal_id)
            if context is None:
                raise _not_found(f"Unknown proposal {proposal_id}")
            proposal = session.proposals[proposal_id].model_copy(deep=True)
            scenario, result = context
            scenario, result = scenario.model_copy(deep=True), result.model_copy(deep=True)
        from backend.app.services.advisory import explain, explanation_input, selector_from_env

        explanation = explain(explanation_input(scenario, result), selector_from_env())
        return {"mode": "simulation", "proposal_id": proposal.id,
                "proposal_revision": proposal.revision, "proposal_status": proposal.status,
                "explanation": _dump(explanation)}

    # --- Mutation engine ---------------------------------------------------------

    def _run(self, session: _Session, action: str, body: Any,
             apply: Callable[[datetime], tuple[dict[str, Any] | None, str | None, bool]],
             idempotency_key: str | None = None, *, replan: bool = True) -> ActionResponse:
        """Apply under lock; ``apply`` returns (result, impact_cause, duplicate).

        A non-null impact cause bumps the scene revision, supersedes open
        proposals and invalidates unconsumed approvals, then auto-replans once.
        """
        fingerprint = _fingerprint(action, body)
        job = None
        failure: SimulationError | None = None
        response = None
        with session.lock:
            replay = self._replay(session, idempotency_key, fingerprint)
            if replay is not None:
                return replay
            now = self._now()
            swept = self._sweep(session, now)
            cause = None
            try:
                result, cause, duplicate = apply(now)
            except SimulationError as error:
                failure = error
            if failure is None:
                signature = self._freshness_signature(session, now)
                if signature != session.freshness_signature:
                    session.freshness_signature = signature
                    cause = cause or "observation_freshness_changed"
                if cause is not None:
                    self._bump(session, cause, now)
            if (swept or cause is not None) and replan and self.auto_recommend:
                job = self._prepare(session, now, cause or "observation_freshness_changed")
            if failure is None:
                response = ActionResponse(action=action, duplicate=duplicate, result=result,
                                          state=self._state(session, now))
                if idempotency_key is not None:
                    self._remember(session, idempotency_key, fingerprint, response)
        if job is not None:
            self._execute(session, job)
        if failure is not None:
            raise failure
        assert response is not None
        if job is not None:
            with session.lock:
                response = response.model_copy(
                    update={"state": self._state(session, self._now())}, deep=True)
                if idempotency_key is not None:
                    self._remember(session, idempotency_key, fingerprint, response)
        return response

    @staticmethod
    def _replay(session: _Session, key: str | None, fingerprint: str) -> ActionResponse | None:
        """Stored response for a reused key; 409 for a different body or in-flight key."""
        if key is None:
            return None
        stored = session.idempotency.get(key)
        if stored is None:
            return None
        if stored.fingerprint != fingerprint:
            raise _conflict("Idempotency-Key was already used with a different request")
        if stored.response is None:
            raise _conflict("A request with this Idempotency-Key is still in progress; retry later")
        return stored.response.model_copy(update={"duplicate": True}, deep=True)

    @staticmethod
    def _remember(session: _Session, key: str, fingerprint: str,
                  response: ActionResponse | None) -> None:
        session.idempotency[key] = _Idempotent(
            fingerprint, response.model_copy(deep=True) if response is not None else None)
        session.idempotency.move_to_end(key)
        while len(session.idempotency) > MAX_IDEMPOTENCY_KEYS:
            session.idempotency.popitem(last=False)

    def _bump(self, session: _Session, cause: str, now: datetime) -> None:
        session.scene_revision += 1
        for proposal in session.proposals.values():
            if proposal.status in OPEN_PROPOSAL_STATUSES:
                self._close(session, proposal, "superseded", cause, now)
        session.current_proposal_id = None

    def _close(self, session: _Session, proposal: SimulationProposal, status: str,
               reason: str, now: datetime) -> None:
        proposal.status = status
        proposal.closed_reason = reason
        session.emit(f"simulation.proposal_{status}",
                     {"proposal_id": proposal.id, "proposal_revision": proposal.revision,
                      "reason": reason}, now)
        approval = session.approvals.get(proposal.approval_id or "")
        if approval is not None and approval.status == "active":
            approval.status = "expired" if status == "expired" else "invalidated"
            approval.invalidated_reason = reason
            session.emit("simulation.approval_invalidated",
                         {"approval_id": approval.id, "proposal_id": proposal.id,
                          "status": approval.status, "reason": reason}, now)
        if session.current_proposal_id == proposal.id:
            session.current_proposal_id = None

    def _sweep(self, session: _Session, now: datetime) -> bool:
        """Time-driven transitions. Returns True if the scene revision bumped."""
        if session.meta is None:
            return False
        bumped = False
        signature = self._freshness_signature(session, now)
        if signature != session.freshness_signature:
            session.freshness_signature = signature
            self._bump(session, "observation_freshness_changed", now)
            bumped = True
        for proposal in session.proposals.values():
            if proposal.status in OPEN_PROPOSAL_STATUSES and now >= proposal.expires_at:
                reason = ("freshness_horizon_reached"
                          if proposal.expires_at < proposal.created_at + self.proposal_ttl
                          else "proposal_ttl_elapsed")
                self._close(session, proposal, "expired", reason, now)
        return bumped

    def _associated_observations(self, session: _Session) -> list[tuple[str, CVObservation]]:
        return [(casualty_id, session.tracks[track_id].latest)
                for casualty_id, track_id in sorted(session.casualty_tracks.items())
                if casualty_id not in session.resolved]

    def _classify(self, instant: datetime, now: datetime) -> str:
        """Mirrors the solver: age < 0 future, age > freshness stale."""
        age = (now - instant).total_seconds()
        return "future" if age < 0 else "stale" if age > self.freshness_seconds else "fresh"

    def _freshness_signature(self, session: _Session, now: datetime) -> tuple:
        signature = []
        for casualty_id, observation in self._associated_observations(session):
            entry = [casualty_id, self._classify(observation.observed_at, now)]
            if observation.track is not None:
                entry.append(self._classify(observation.track.last_seen_at, now))
            signature.append(tuple(entry))
        return tuple(signature)

    def _freshness_horizon(self, session: _Session, now: datetime) -> datetime | None:
        """Earliest future instant at which any freshness classification can change."""
        window = timedelta(seconds=self.freshness_seconds)
        instants = []
        for _, observation in self._associated_observations(session):
            stamps = [observation.observed_at]
            if observation.track is not None:
                stamps.append(observation.track.last_seen_at)
            instants.extend(stamp + offset for stamp in stamps for offset in (timedelta(0), window))
        upcoming = [instant for instant in instants if instant > now]
        return min(upcoming) if upcoming else None

    # --- Recommendation ----------------------------------------------------------

    def _prepare(self, session: _Session, now: datetime, trigger: str,
                 solver_name: str | None = None) -> _SolverJob | None:
        from optimization.models import OptimizationRequest

        scenario = session.scenario(include_resolved=False)
        present = {casualty.id for casualty in scenario.casualties}
        active = [Assignment(casualty_id=item.casualty_id, responder_id=item.responder_id,
                             route=item.route.model_copy(deep=True) if item.route else None)
                  for item in session.active_assignments() if item.casualty_id in present]
        observations = {
            casualty_id: session.tracks[track_id].latest.model_copy(deep=True)
            for casualty_id, track_id in session.casualty_tracks.items() if casualty_id in present
        }
        request = OptimizationRequest(
            scenario=scenario,
            required_capabilities={cid: list(tags) for cid, tags in session.required_capabilities.items()
                                   if cid in present and tags},
            active_assignments=active, observations=observations,
            scene_revision=session.scene_revision, now=now,
            solver=solver_name or self.solver_name,
            freshness_seconds=self.freshness_seconds, min_confidence=self.min_confidence,
        )
        session.emit("simulation.replan_requested",
                     {"trigger": trigger, "scene_revision": session.scene_revision,
                      "actor": "system" if not trigger.startswith("explicit:") else trigger[9:]},
                     now)
        return _SolverJob(session.epoch, session.scene_revision, trigger, request, now)

    def _execute(self, session: _Session, job: _SolverJob) -> SimulationProposal | RecommendationState | None:
        """Run the solver unlocked; install only if the scene is unchanged.

        If time alone changed observation freshness during the solve, the sweep
        bumps the revision and the solve is retried a bounded number of times.
        A stale result caused by another mutation is discarded: that mutation
        schedules its own replan.
        """
        for _ in range(EXPLICIT_REPLAN_ATTEMPTS):
            error = None
            result = None
            try:
                result = self.solver(job.request.model_copy(deep=True))
            except Exception as exc:  # solver faults must never corrupt state
                error = f"{type(exc).__name__}: {exc}"
            with session.lock:
                now = self._now()
                swept = self._sweep(session, now)
                if session.epoch == job.epoch and session.scene_revision == job.revision:
                    return self._install(session, job, result, error)
                if not (swept and self.auto_recommend):
                    return None
                job = self._prepare(session, now, "observation_freshness_changed")
        return None

    def _install(self, session: _Session, job: _SolverJob, result: Any,
                 error: str | None) -> SimulationProposal | RecommendationState:
        now = self._now()
        for proposal in session.proposals.values():
            if proposal.status in OPEN_PROPOSAL_STATUSES:
                self._close(session, proposal, "superseded", f"replaced_by_replan:{job.trigger}", now)
        active = session.active_assignments()
        busy_casualties = {item.casualty_id for item in active} | session.resolved
        busy_responders = {item.responder_id for item in active}
        new: list[Assignment] = []
        preserved: list[Assignment] = []
        if result is not None and error is None:
            if getattr(result, "scene_revision", job.revision) != job.revision:
                error = "Solver result scene_revision does not match request"
            else:
                seen_casualties: set[str] = set()
                seen_responders: set[str] = set()
                for assignment in result.assignments:
                    if (assignment.casualty_id in busy_casualties
                            or assignment.responder_id in busy_responders):
                        preserved.append(assignment.model_copy(deep=True))
                        continue
                    responder = session.responders.get(assignment.responder_id)
                    if (assignment.casualty_id not in session.casualties or responder is None
                            or responder.status != "available"
                            or assignment.casualty_id in seen_casualties
                            or assignment.responder_id in seen_responders):
                        error = "Solver returned an assignment that violates current state"
                        new = []
                        break
                    seen_casualties.add(assignment.casualty_id)
                    seen_responders.add(assignment.responder_id)
                    new.append(assignment.model_copy(deep=True))
                for assignment in getattr(result, "preserved_assignments", None) or []:
                    if assignment not in preserved:
                        preserved.append(assignment.model_copy(deep=True))
        proposal = None
        if error is None and new:
            expires_at = now + self.proposal_ttl
            horizon = self._freshness_horizon(session, now)
            if horizon is not None and horizon < expires_at:
                expires_at = horizon
            proposal = SimulationProposal(
                id=session.next_id("proposal"), scenario_id=session.scenario_id,
                epoch=session.epoch, revision=job.revision, status="pending_approval",
                assignments=new, preserved_assignments=preserved,
                unassigned_casualty_ids=[cid for cid in result.unassigned_casualty_ids
                                         if cid not in busy_casualties],
                reasons={key: list(value) for key, value in result.reasons.items()},
                rationale=list(result.rationale), engine=result.engine,
                runtime_ms=result.runtime_ms, map_revision=result.map_revision,
                objective_values=dict(result.objective_values),
                recommendation=result.metadata.model_copy(deep=True),
                created_at=now, expires_at=expires_at,
            )
            session.proposals[proposal.id] = proposal
            session.proposal_context[proposal.id] = (job.request.scenario.model_copy(deep=True),
                                                    result.model_copy(deep=True))
            session.current_proposal_id = proposal.id
        recommendation = RecommendationState(
            scene_revision=job.revision, computed_at=now, trigger=job.trigger,
            proposal_id=proposal.id if proposal else None,
            engine=getattr(result, "engine", None) if error is None else None,
            unassigned_casualty_ids=[] if result is None or error else
            [cid for cid in result.unassigned_casualty_ids if cid not in busy_casualties],
            reasons={} if result is None or error else
            {key: list(value) for key, value in result.reasons.items()},
            error=error,
        )
        session.last_recommendation = recommendation
        session.emit("simulation.recommendation_computed", _dump(recommendation), now)
        if proposal is not None:
            session.emit("simulation.proposal_created", _dump(proposal), now)
            return proposal.model_copy(deep=True)
        return recommendation.model_copy(deep=True)

    # --- Public mutations ---------------------------------------------------------

    def reset(self, scenario_id: str, request: ResetRequest | None = None,
              idempotency_key: str | None = None, *, only_if_unloaded: bool = False) -> ActionResponse:
        """New epoch: clears all simulation records but never rewinds the event
        sequence, scene revision, or ID counters, so subscribers stay monotonic."""
        request = request or ResetRequest()
        if request.scenario is not None:
            scenario = request.scenario.model_copy(deep=True)
            if scenario.id != scenario_id:
                raise _invalid("Scenario id must match the path scenario_id")
        elif scenario_id == DEMO_SCENARIO_ID:
            scenario = load_demo_scenario()
        else:
            raise _not_found("Only the demo scenario loads without a supplied synthetic scenario")
        if request.add_demo_map and scenario.scene_map is None:
            scenario = scenario.model_copy(update={"scene_map": demo_scene_map(scenario.frame_id)})
            scenario = Scenario.model_validate(_dump(scenario))
        casualty_ids = {casualty.id for casualty in scenario.casualties}
        if set(request.required_capabilities) - casualty_ids:
            raise _invalid("required_capabilities must reference scenario casualty IDs")
        session = self._get_or_create(scenario_id)

        def apply(now: datetime):
            if only_if_unloaded and session.meta is not None:
                return {"epoch": session.epoch, "scene_revision": session.scene_revision}, None, True
            session.epoch += 1
            session.meta = scenario.model_copy(update={"casualties": [], "responders": [],
                                                       "scene_map": None})
            session.casualties = {c.id: c.model_copy(deep=True) for c in scenario.casualties}
            session.responders = {r.id: r.model_copy(deep=True) for r in scenario.responders}
            session.scene_map = scenario.scene_map.model_copy(deep=True) if scenario.scene_map else None
            session.required_capabilities = {
                cid: sorted(set(request.required_capabilities.get(cid, []))) for cid in casualty_ids}
            for name in ("tracks", "casualty_tracks", "observation_fingerprints",
                         "responder_reported_at", "responder_preferences", "proposals",
                         "proposal_context", "approvals", "dispatches", "assignments",
                         "device_events"):
                getattr(session, name).clear()
            session.resolved.clear()
            session.map_revisions = ({scenario.scene_map.revision: _map_fingerprint(scenario.scene_map)}
                                     if scenario.scene_map else {})
            session.freshness_signature = ()
            session.current_proposal_id = None
            session.last_recommendation = None
            revision = session.scene_revision + 1  # applied by the "reset" impact bump
            session.emit("simulation.reset", {"epoch": session.epoch, "scene_revision": revision,
                                              "scenario": _dump(session.scenario())}, now)
            return {"epoch": session.epoch, "scene_revision": revision}, "reset", False

        return self._run(session, "reset", _dump(request), apply, idempotency_key)

    def ingest_observation(self, scenario_id: str, observation: CVObservation,
                           idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            fingerprint = _fingerprint(_dump(observation))
            known = session.observation_fingerprints.get(observation.id)
            payload = {"observation_id": observation.id}
            if known is not None:
                if known != fingerprint:
                    raise _conflict(f"Observation {observation.id} was already received with different content")
                return payload, None, True
            self._check_frame(session, observation.position)
            if observation.observed_at > now + self.skew:
                raise _invalid("observed_at is in the future")
            cause = None
            casualty_id = None
            track = observation.track
            if track is not None:
                record = session.tracks.get(track.track_id)
                if record is not None:
                    previous = record.latest
                    assert previous.track is not None
                    if observation.observed_at < previous.observed_at:
                        raise _conflict("Stale observation: older than the track's latest update")
                    if track.first_seen_at != previous.track.first_seen_at:
                        raise _conflict("Track identity conflict: first_seen_at differs for this track_id")
                    if track.last_seen_at < previous.track.last_seen_at:
                        raise _conflict("Stale observation: last_seen_at regresses")
                    if track.state != "visible" and (
                            observation.position != previous.position
                            or observation.confidence != previous.confidence
                            or track.last_seen_at != previous.track.last_seen_at):
                        raise _conflict(f"A {track.state} report must retain the last actual "
                                        "detection's position, confidence and last_seen_at")
                    record.latest = observation.model_copy(deep=True)
                    record.count += 1
                    casualty_id = record.casualty_id
                    if casualty_id is not None:
                        # Any authoritative position change bumps the revision; freshness
                        # transitions (e.g. a stale track restored) are caught by the
                        # freshness signature in _run.
                        impactful = (
                            session.casualties[casualty_id].position != observation.position
                            or previous.track.state != track.state
                            or (previous.confidence >= self.min_confidence)
                            != (observation.confidence >= self.min_confidence)
                        )
                        self._set_casualty_position(session, casualty_id, observation.position)
                        if impactful:
                            cause = f"observation:{track.track_id}"
                else:
                    session.tracks[track.track_id] = _Track(track.track_id,
                                                            observation.model_copy(deep=True))
            session.observation_fingerprints[observation.id] = fingerprint
            session.emit("simulation.observation_received",
                         {"observation": _dump(observation),
                          "track_id": track.track_id if track else None,
                          "casualty_id": casualty_id}, now)
            payload.update(track_id=track.track_id if track else None, casualty_id=casualty_id)
            return payload, cause, False

        return self._run(session, "observation", _dump(observation), apply, idempotency_key)

    def associate_track(self, scenario_id: str, request: AssociationRequest,
                        idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            record = session.tracks.get(request.track_id)
            if record is None:
                raise _not_found(f"Unknown track {request.track_id}")
            result = {"track_id": record.track_id, "casualty_id": request.casualty_id,
                      "created": False}
            if record.casualty_id is not None:
                if record.casualty_id == request.casualty_id and not request.create_casualty:
                    return result, None, True
                raise _conflict(f"Track is already associated with {record.casualty_id}")
            if request.create_casualty:
                if request.casualty_id in session.casualties or request.casualty_id in session.responders:
                    raise _conflict(f"Entity ID {request.casualty_id} already exists")
                casualty = Casualty(id=request.casualty_id,
                                    position=record.latest.position.model_copy(deep=True),
                                    medical_assessment=None)
                session.casualties[casualty.id] = casualty
                session.required_capabilities[casualty.id] = []
                session.emit("simulation.casualty_created",
                             {"casualty": _dump(casualty), "track_id": record.track_id,
                              "actor": request.actor}, now)
                result["created"] = True
            else:
                if request.casualty_id not in session.casualties:
                    raise _not_found(f"Unknown casualty {request.casualty_id}")
                existing = session.casualty_tracks.get(request.casualty_id)
                if existing is not None:
                    raise _conflict(f"Casualty is already associated with track {existing}")
                self._set_casualty_position(session, request.casualty_id, record.latest.position)
            record.casualty_id = request.casualty_id
            session.casualty_tracks[request.casualty_id] = record.track_id
            session.emit("simulation.track_associated", {**result, "actor": request.actor}, now)
            return result, f"association:{record.track_id}", False

        return self._run(session, "association", _dump(request), apply, idempotency_key)

    def record_assessment(self, scenario_id: str, casualty_id: str, assessment: MedicalAssessment,
                          idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            casualty = session.casualties.get(casualty_id)
            if casualty is None:
                raise _not_found(f"Unknown casualty {casualty_id}")
            if assessment.assessed_at > now + self.skew:
                raise _invalid("assessed_at is in the future")
            current = casualty.medical_assessment
            result = {"casualty_id": casualty_id, "medical_assessment": _dump(assessment)}
            if current is not None:
                if current == assessment:
                    return result, None, True
                if assessment.assessed_at <= current.assessed_at:
                    raise _conflict("Stale assessment: not newer than the recorded assessment")
            casualty.medical_assessment = assessment.model_copy(deep=True)
            session.emit("simulation.assessment_recorded", result, now)
            return result, f"assessment:{casualty_id}", False

        return self._run(session, "assessment", [casualty_id, _dump(assessment)], apply,
                         idempotency_key)

    def update_responder(self, scenario_id: str, responder_id: str, request: ResponderUpdateRequest,
                         idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            responder = session.responders.get(responder_id)
            if responder is None:
                raise _not_found(f"Unknown responder {responder_id}")
            if request.reported_at is not None:
                if request.reported_at > now + self.skew:
                    raise _invalid("reported_at is in the future")
                last = session.responder_reported_at.get(responder_id)
                if last is not None and request.reported_at < last:
                    raise _conflict("Stale responder update: older than the last report")
            if request.position is not None:
                self._check_frame(session, request.position)
            updated = responder.model_copy(deep=True)
            if request.position is not None:
                updated.position = request.position.model_copy(deep=True)
            if request.capabilities is not None:
                updated.capabilities = sorted(set(request.capabilities))
            if request.status is not None:
                if any(item.responder_id == responder_id for item in session.active_assignments()):
                    # Active simulated work is preserved; remember the post-task preference.
                    session.responder_preferences[responder_id] = request.status
                    updated.status = "offline" if request.status == "offline" else "busy"
                else:
                    updated.status = request.status
            if request.reported_at is not None:
                session.responder_reported_at[responder_id] = request.reported_at
            result = {"responder": _dump(updated),
                      "status_after_active_work": session.responder_preferences.get(responder_id)}
            if updated == responder:
                return result, None, True
            session.responders[responder_id] = updated
            session.emit("simulation.responder_updated", result, now)
            return result, f"responder:{responder_id}", False

        return self._run(session, "responder_update", [responder_id, _dump(request)], apply,
                         idempotency_key)

    def update_map(self, scenario_id: str, scene_map: SceneMap,
                   idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            assert session.meta is not None
            if scene_map.frame_id != session.meta.frame_id:
                raise _invalid("Map frame_id must match the scenario frame")
            result = {"map_revision": scene_map.revision}
            current = session.scene_map
            if current is not None and current == scene_map:
                return result, None, True
            known = session.map_revisions.get(scene_map.revision)
            if known is not None and known != _map_fingerprint(scene_map):
                raise _conflict("Map revision was already used this epoch with different "
                                "geometry; every geometry/occupancy change needs a new revision")
            session.map_revisions[scene_map.revision] = _map_fingerprint(scene_map)
            session.scene_map = scene_map.model_copy(deep=True)
            session.emit("simulation.map_updated",
                         {"previous_revision": current.revision if current else None,
                          "scene_map": _dump(scene_map)}, now)
            return result, f"map:{scene_map.revision}", False

        return self._run(session, "map_update", _dump(scene_map), apply, idempotency_key)

    def set_required_capabilities(self, scenario_id: str, casualty_id: str,
                                  request: RequiredCapabilitiesRequest,
                                  idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            if casualty_id not in session.casualties:
                raise _not_found(f"Unknown casualty {casualty_id}")
            tags = sorted(set(request.capabilities))
            result = {"casualty_id": casualty_id, "required_capabilities": tags}
            if session.required_capabilities.get(casualty_id, []) == tags:
                return result, None, True
            session.required_capabilities[casualty_id] = tags
            session.emit("simulation.capabilities_updated", {**result, "actor": request.actor}, now)
            return result, f"capabilities:{casualty_id}", False

        return self._run(session, "required_capabilities", [casualty_id, _dump(request)], apply,
                         idempotency_key)

    def replan(self, scenario_id: str, request: ReplanRequest,
               idempotency_key: str | None = None) -> ActionResponse:
        """Explicit replan: supersedes open proposals at the current revision.

        Retries compare-and-set a bounded number of times, then returns 409.
        """
        session = self._session(scenario_id)
        fingerprint = _fingerprint("replan", _dump(request))
        with session.lock:
            replay = self._replay(session, idempotency_key, fingerprint)
            if replay is not None:
                return replay
            if idempotency_key is not None:  # reserve atomically before unlocking
                self._remember(session, idempotency_key, fingerprint, None)
        trigger = f"explicit:{request.actor}" + (f":{request.reason}" if request.reason else "")
        try:
            outcome = None
            for _ in range(EXPLICIT_REPLAN_ATTEMPTS):
                with session.lock:
                    now = self._now()
                    self._sweep(session, now)
                    job = self._prepare(session, now, trigger, request.solver)
                outcome = self._execute(session, job)
                if outcome is not None:
                    break
            if outcome is None:
                raise _conflict("Scene changed during every replan attempt; retry")
        except BaseException:
            if idempotency_key is not None:
                with session.lock:
                    stored = session.idempotency.get(idempotency_key)
                    if stored is not None and stored.response is None:
                        del session.idempotency[idempotency_key]
            raise
        with session.lock:
            response = ActionResponse(action="replan", result=_dump(outcome),
                                      state=self._state(session, self._now()))
            if idempotency_key is not None:
                self._remember(session, idempotency_key, fingerprint, response)
        return response

    def decide(self, scenario_id: str, proposal_id: str, request: DecisionRequest,
               idempotency_key: str) -> ActionResponse:
        session = self._session(scenario_id)

        def apply(now: datetime):
            proposal = session.proposals.get(proposal_id)
            if proposal is None:
                raise _not_found(f"Unknown proposal {proposal_id}")
            if proposal.status != "pending_approval":
                raise _conflict(f"Proposal is {proposal.status}, not pending_approval")
            if request.proposal_revision != proposal.revision:
                raise _conflict("Stale proposal revision: decide on the exact current revision")
            if proposal.revision != session.scene_revision:
                raise _conflict("Proposal no longer matches the current scene revision")
            approval = SimulationApproval(
                id=session.next_id("approval"), proposal_id=proposal.id,
                proposal_revision=proposal.revision, decision=request.decision,
                approved_by=request.actor, operator_id=request.actor, decided_at=now,
                expires_at=proposal.expires_at,
                status="active" if request.decision == "approve" else "recorded",
            )
            session.approvals[approval.id] = approval
            proposal.approval_id = approval.id
            proposal.status = "approved" if request.decision == "approve" else "rejected"
            if request.decision == "reject":
                proposal.closed_reason = f"rejected_by:{request.actor}"
                session.current_proposal_id = None
            session.emit("simulation.decision_recorded", _dump(approval), now)
            return _dump(approval), None, False

        return self._run(session, "decision", [proposal_id, _dump(request)], apply, idempotency_key)

    def dispatch(self, scenario_id: str, request: DispatchRequest,
                 idempotency_key: str) -> ActionResponse:
        """SIMULATION dispatch: atomically consumes one approval. No device I/O."""
        session = self._session(scenario_id)

        def apply(now: datetime):
            proposal = session.proposals.get(request.proposal_id)
            if proposal is None:
                raise _not_found(f"Unknown proposal {request.proposal_id}")
            approval = session.approvals.get(request.approval_id)
            if approval is None:
                raise _not_found(f"Unknown approval {request.approval_id}")
            if approval.proposal_id != proposal.id:
                raise _conflict("Approval does not belong to this proposal")
            if request.proposal_revision != proposal.revision or approval.proposal_revision != proposal.revision:
                raise _conflict("Stale proposal revision")
            if approval.decision != "approve":
                raise _conflict("Proposal was rejected; nothing to dispatch")
            if approval.status == "consumed":
                raise _conflict("Approval was already consumed by a dispatch")
            if approval.status != "active":
                raise _conflict(f"Approval is {approval.status}: {approval.invalidated_reason}")
            if proposal.status != "approved" or proposal.revision != session.scene_revision:
                raise _conflict(f"Proposal is {proposal.status} at a stale scene revision")
            active = session.active_assignments()
            for assignment in proposal.assignments:
                responder = session.responders.get(assignment.responder_id)
                if (assignment.casualty_id not in session.casualties
                        or assignment.casualty_id in session.resolved
                        or responder is None or responder.status != "available"
                        or any(item.casualty_id == assignment.casualty_id
                               or item.responder_id == assignment.responder_id for item in active)):
                    raise _conflict("Proposal no longer matches current entity availability")
            dispatch_id = session.next_id("dispatch")
            created = []
            for assignment in proposal.assignments:
                record = SimulatedAssignment(
                    id=session.next_id("assignment"), dispatch_id=dispatch_id,
                    command_id=session.next_id("command"),
                    device_id=f"sim-device-{assignment.responder_id}",
                    casualty_id=assignment.casualty_id, responder_id=assignment.responder_id,
                    route=assignment.route.model_copy(deep=True) if assignment.route else None,
                    status="dispatched", created_at=now,
                )
                session.assignments[record.id] = record
                session.responders[assignment.responder_id].status = "busy"
                created.append(record)
            dispatch = SimulationDispatch(
                id=dispatch_id, proposal_id=proposal.id, proposal_revision=proposal.revision,
                approval_id=approval.id, dispatched_by=request.actor, dispatched_at=now,
                assignment_ids=[record.id for record in created],
            )
            session.dispatches[dispatch.id] = dispatch
            approval.status = "consumed"
            proposal.status = "dispatched"
            proposal.dispatch_id = dispatch.id
            session.current_proposal_id = None
            result = {"dispatch": _dump(dispatch), "assignments": [_dump(r) for r in created]}
            session.emit("simulation.dispatch_created", result, now)
            return result, f"dispatch:{dispatch.id}", False

        return self._run(session, "dispatch", _dump(request), apply, idempotency_key)

    def ingest_device_event(self, scenario_id: str, event: SimulatedDeviceEvent,
                            idempotency_key: str | None = None) -> ActionResponse:
        session = self._session(scenario_id)
        return self._run(session, "device_event", _dump(event),
                         lambda now: self._apply_device_event(session, scenario_id, event, now),
                         idempotency_key)

    def simulate_device_event(self, scenario_id: str, assignment_id: str,
                              request: SimulateDeviceRequest,
                              idempotency_key: str | None = None) -> ActionResponse:
        """Build and ingest a correlated SIMULATED device report atomically.

        The default event ID is deterministic per assignment and kind. When that
        ID was already ingested and no ``occurred_at`` is given, the stored
        timestamp is reused, so retrying the same helper request (with or
        without the same Idempotency-Key) replays instead of conflicting.
        """
        session = self._session(scenario_id)

        def apply(now: datetime):
            record = session.assignments.get(assignment_id)
            if record is None:
                raise _not_found(f"Unknown assignment {assignment_id}")
            event_id = request.event_id or f"sim-device-event-{assignment_id}-{request.kind}"
            stored = session.device_events.get(event_id)
            occurred_at = request.occurred_at or (stored.occurred_at if stored else now)
            event = SimulatedDeviceEvent(
                id=event_id, scenario_id=scenario_id, device_id=record.device_id,
                dispatch_id=record.dispatch_id, command_id=record.command_id,
                casualty_id=record.casualty_id, responder_id=record.responder_id,
                kind=request.kind, occurred_at=occurred_at, mode="simulation",
            )
            return self._apply_device_event(session, scenario_id, event, now)

        return self._run(session, "simulate_device_event", [assignment_id, _dump(request)],
                         apply, idempotency_key)

    def _apply_device_event(self, session: _Session, scenario_id: str,
                            event: SimulatedDeviceEvent, now: datetime):
        if event.scenario_id != scenario_id:
            raise _invalid("Device event scenario_id must match the path")
        known = session.device_events.get(event.id)
        if known is not None:
            if known != event:
                raise _conflict(f"Device event {event.id} was already received with different content")
            return {"event_id": event.id, "state_changed": False,
                    "device_event": _dump(known)}, None, True
        dispatch = session.dispatches.get(event.dispatch_id)
        if dispatch is None:
            raise _not_found(f"Unknown dispatch {event.dispatch_id}")
        record = next((session.assignments[aid] for aid in dispatch.assignment_ids
                       if session.assignments[aid].command_id == event.command_id), None)
        if record is None:
            raise _conflict("command_id does not belong to this dispatch")
        if (record.casualty_id, record.responder_id, record.device_id) != (
                event.casualty_id, event.responder_id, event.device_id):
            raise _conflict("Device event does not correlate with the dispatched assignment")
        if event.occurred_at > now + self.skew:
            raise _invalid("occurred_at is in the future")
        if event.occurred_at < dispatch.dispatched_at - self.skew:
            raise _conflict("Stale device event: occurred before the dispatch")
        for kind in ("delivered", "acknowledged", "completed"):
            recorded = getattr(record, f"{kind}_at")
            if recorded is None or kind == event.kind:
                continue
            earlier = MILESTONE_RANK[kind] < MILESTONE_RANK[event.kind]
            if (recorded > event.occurred_at) if earlier else (recorded < event.occurred_at):
                raise _conflict(f"{event.kind} at {event.occurred_at.isoformat()} is inconsistent "
                                f"with recorded {kind} at {recorded.isoformat()}")
        session.device_events[event.id] = event.model_copy(deep=True)
        record.device_event_ids.append(event.id)
        stamp = f"{event.kind}_at"
        result = {"event_id": event.id, "assignment_id": record.id, "state_changed": False,
                  "device_event": _dump(event)}
        if getattr(record, stamp) is not None:
            return result, None, False  # milestone retry under a new event ID
        setattr(record, stamp, event.occurred_at)
        if MILESTONE_RANK[event.kind] > MILESTONE_RANK[record.status]:
            record.status = event.kind  # out-of-order reports never regress status
        result["state_changed"] = True
        session.emit(f"simulation.device_{event.kind}",
                     {"device_event": _dump(event), "assignment": _dump(record)}, now)
        if event.kind == "completed":
            responder = session.responders[record.responder_id]
            responder.status = session.responder_preferences.pop(record.responder_id, "available")
            session.resolved.add(record.casualty_id)
            session.emit("simulation.responder_released",
                         {"responder_id": responder.id, "status": responder.status,
                          "assignment_id": record.id}, now)
        return result, f"device:{event.kind}:{record.id}", False

    # --- Helpers -----------------------------------------------------------------

    def _check_frame(self, session: _Session, position: Position) -> None:
        assert session.meta is not None
        if position.frame_id != session.meta.frame_id:
            raise _invalid("Position frame_id must match the scenario frame")

    @staticmethod
    def _set_casualty_position(session: _Session, casualty_id: str, position: Position) -> None:
        session.casualties[casualty_id].position = position.model_copy(deep=True)

    def _state(self, session: _Session, now: datetime) -> SimulationState:
        tracks = [TrackRecord(track_id=t.track_id, casualty_id=t.casualty_id,
                              latest_observation=t.latest.model_copy(deep=True),
                              observation_count=t.count)
                  for t in sorted(session.tracks.values(), key=lambda t: t.track_id)]
        return SimulationState(
            scenario_id=session.scenario_id, epoch=session.epoch,
            scene_revision=session.scene_revision, sequence=session.events.sequence,
            generated_at=now, scenario=session.scenario(),
            required_capabilities={cid: list(tags) for cid, tags in
                                   sorted(session.required_capabilities.items())},
            tracks=tracks, resolved_casualty_ids=sorted(session.resolved),
            current_proposal_id=session.current_proposal_id,
            proposals=[p.model_copy(deep=True) for p in session.proposals.values()],
            approvals=[a.model_copy(deep=True) for a in session.approvals.values()],
            dispatches=[d.model_copy(deep=True) for d in session.dispatches.values()],
            assignments=[a.model_copy(deep=True) for a in session.assignments.values()],
            last_recommendation=(session.last_recommendation.model_copy(deep=True)
                                 if session.last_recommendation else None),
        )
