"""evolution_cycle.py — Evolution cycle with persistent failure blacklist + circuit breaker.

Implements:
  * JSON-backed blacklist of failed improvements (persist_fails/blacklist.json).
  * SHA256 fingerprinting (target_file|description) to identify improvements.
  * Blacklist filtering before validation (skip when fail_count >= 2).
  * Consecutive full-failure counter with circuit breaker -> wide_mutation flag
    -> planner feedback 'switch_direction: True'.
"""

import json
import hashlib
import os


# ---------------------------------------------------------------------------
# Blacklist persistence helpers
# ---------------------------------------------------------------------------

def _blacklist_path() -> str:
    """Return absolute path of the blacklist file, ensuring its directory exists."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'persist_fails')
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, 'blacklist.json')


def _fingerprint(improvement_dict: dict) -> str:
    """Compute a stable SHA256 fingerprint for an improvement.

    Normalises ``target_file`` and ``description`` via ``strip().lower()``
    and concatenates them with ``'|'``.  Returns the first 16 hex chars of
    the SHA256 digest.
    """
    target = str(improvement_dict.get('target_file', '')).strip().lower()
    description = str(improvement_dict.get('description', '')).strip().lower()
    canonical = '{}|{}'.format(target, description)
    digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    return digest[:16]


def _load_blacklist() -> dict:
    """Load the persistent failure blacklist from disk.

    Returns an empty dict when the file is missing or corrupt.
    """
    path = _blacklist_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _save_blacklist(blacklist: dict) -> None:
    """Persist the failure blacklist to disk as pretty-printed JSON."""
    path = _blacklist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        json.dump(blacklist, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Evolution cycle
# ---------------------------------------------------------------------------

class EvolutionCycle:
    """Drives propose -> validate -> feedback loops with persistent learning."""

    #: Number of consecutive fully-failed rounds that trigger the circuit breaker.
    CIRCUIT_BREAKER_THRESHOLD = 3

    def __init__(self, planner=None, validator=None, logger=None):
        self._planner = planner
        self._validator = validator
        self._logger = logger

        # Circuit-breaker state.
        self._consecutive_full_failures = 0
        self._wide_mutation = False

    # -- logging convenience -------------------------------------------------

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(message)
        else:
            print(message)

    # -- blacklist interaction ----------------------------------------------

    def _is_blacklisted(self, blacklist: dict, fingerprint: str) -> bool:
        """Return True when the fingerprint has failed at least twice."""
        entry = blacklist.get(fingerprint)
        if not isinstance(entry, dict):
            return False
        return int(entry.get('fail_count', 0)) >= 2

    def _record_failure(self, blacklist: dict, fingerprint: str, reason: str) -> None:
        """Increment fail_count for *fingerprint* and persist the blacklist."""
        entry = blacklist.get(fingerprint)
        if not isinstance(entry, dict):
            entry = {'reason': reason, 'timestamp': None, 'fail_count': 0}
        entry['reason'] = reason
        try:
            from datetime import datetime, timezone
            entry['timestamp'] = datetime.now(timezone.utc).isoformat()
        except Exception:  # pragma: no cover - datetime always available
            entry['timestamp'] = None
        entry['fail_count'] = int(entry.get('fail_count', 0)) + 1
        blacklist[fingerprint] = entry
        _save_blacklist(blacklist)

    # -- planner invocation ---------------------------------------------------

    def _call_planner(self, context: dict):
        """Invoke the planner, injecting circuit-breaker feedback when active."""
        planner_kwargs = dict(context) if context else {}
        if self._wide_mutation:
            planner_kwargs['feedback'] = 'switch_direction'
            planner_kwargs['switch_direction'] = True
        if self._planner is None:
            raise RuntimeError('No planner configured')
        return self._planner(planner_kwargs)

    # -- propose --------------------------------------------------------------

    def propose(self, context: dict = None):
        """Generate improvements, filter the blacklist, then validate them.

        Returns a dict summarising the round::

            {
              'proposed': [ ...raw improvements... ],
              'accepted': [ ...successful improvements... ],
              'rejected': [ {'improvement': ..., 'reason': ...}, ... ],
              'skipped':  [ {'improvement': ..., 'fingerprint': ...}, ... ],
              'wide_mutation': bool,
            }
        """
        raw_improvements = self._call_planner(context)
        if raw_improvements is None:
            raw_improvements = []
        if not isinstance(raw_improvements, list):
            raw_improvements = [raw_improvements]

        blacklist = _load_blacklist()

        # --- Filter blacklisted improvements before validation -------------
        candidates = []
        skipped = []
        for improvement in raw_improvements:
            fp = _fingerprint(improvement)
            if self._is_blacklisted(blacklist, fp):
                self._log('skipped blacklisted improvement: {}'.format(fp))
                skipped.append({'improvement': improvement, 'fingerprint': fp})
                continue
            candidates.append(improvement)

        # --- Validate ------------------------------------------------------
        accepted = []
        rejected = []
        for improvement in candidates:
            fp = _fingerprint(improvement)
            try:
                if self._validator is not None:
                    ok, reason = self._validator(improvement)
                else:
                    ok, reason = True, None
            except Exception as exc:  # noqa: BLE001 - treat exceptions as failures
                ok, reason = False, str(exc)

            if ok:
                accepted.append(improvement)
            else:
                reason_text = reason if reason else 'validation failed'
                rejected.append({'improvement': improvement, 'reason': reason_text})
                self._record_failure(blacklist, fp, reason_text)
                self._log('validation failed for {}: {}'.format(fp, reason_text))

        # --- Update circuit-breaker state ---------------------------------
        round_failed_completely = (
            len(candidates) > 0 and len(accepted) == 0 and len(rejected) > 0
        )
        if round_failed_completely:
            self._consecutive_full_failures += 1
        else:
            self._consecutive_full_failures = 0

        if self._consecutive_full_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            if not self._wide_mutation:
                self._log(
                    'circuit breaker tripped after {} consecutive full failures; '
                    'switching planner direction'.format(self._consecutive_full_failures)
                )
            self._wide_mutation = True

        return {
            'proposed': raw_improvements,
            'accepted': accepted,
            'rejected': rejected,
            'skipped': skipped,
            'wide_mutation': self._wide_mutation,
        }

    # -- round loop -----------------------------------------------------------

    def run(self, rounds: int = 1, context: dict = None):
        """Execute *rounds* propose/validate cycles and collect summaries."""
        summaries = []
        for _ in range(max(1, int(rounds))):
            summary = self.propose(context)
            summaries.append(summary)
        return summaries