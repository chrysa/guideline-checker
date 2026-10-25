"""Profile resolution: explicit from the catalog, or auto-detected from the git tree.

Detection is tri-state per profile — matched, not matched, or undetermined (no tree, or a
truncated tree that cannot prove an absence). The implicit profile ``all`` always matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from guideline_checker.fleet.scan.collect import TreeSnapshot
from guideline_checker.fleet.scan.globbing import glob_matches
from guideline_checker.fleet.scan.models import IMPLICIT_PROFILE, DetectSpec, ProfileDef, Status


class Detection(StrEnum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True)
class ProfileResolution:
    detections: dict[str, Detection]
    explicit: bool

    @property
    def matched(self) -> list[str]:
        return sorted(name for name, d in self.detections.items() if d is Detection.MATCHED)

    @property
    def status(self) -> Status:
        """``pass`` when every profile is determined, ``unknown`` otherwise."""
        undetermined = any(d is Detection.UNDETERMINED for d in self.detections.values())
        return Status.UNKNOWN if undetermined else Status.PASS

    def applicability(self, rule_profiles: list[str]) -> Detection:
        """MATCHED if any rule profile matched, UNDETERMINED if one is unknown, else NOT_MATCHED."""
        states = [self.detections.get(name, Detection.NOT_MATCHED) for name in rule_profiles]
        if Detection.MATCHED in states:
            return Detection.MATCHED
        if Detection.UNDETERMINED in states:
            return Detection.UNDETERMINED
        return Detection.NOT_MATCHED


def resolve_profiles(
    *, profiles: dict[str, ProfileDef], tree: TreeSnapshot | None, explicit: list[str] | None
) -> ProfileResolution:
    """Resolve the repo's profiles. ``explicit`` (from the catalog) bypasses detection."""
    if explicit is not None:
        detections = dict.fromkeys(profiles, Detection.NOT_MATCHED)
        detections.update(dict.fromkeys(explicit, Detection.MATCHED))
        detections[IMPLICIT_PROFILE] = Detection.MATCHED
        return ProfileResolution(detections=detections, explicit=True)
    positive = {
        name: _detect(spec=p.detect, tree=tree) for name, p in profiles.items() if not p.detect.none_of_profiles
    }
    detections = dict(positive)
    for name, profile in profiles.items():
        refs = profile.detect.none_of_profiles
        if refs:
            detections[name] = _negate(states=[positive.get(r, Detection.UNDETERMINED) for r in refs])
    detections[IMPLICIT_PROFILE] = Detection.MATCHED
    return ProfileResolution(detections=detections, explicit=False)


def _detect(*, spec: DetectSpec, tree: TreeSnapshot | None) -> Detection:
    if tree is None:
        return Detection.UNDETERMINED
    criteria: list[bool] = []
    if spec.any_file:
        criteria.append(any(tree.has(p) for p in spec.any_file))
    if spec.all_files:
        criteria.append(all(tree.has(p) for p in spec.all_files))
    if spec.any_glob:
        paths = tree.all_paths
        criteria.append(any(glob_matches(pattern=g, paths=paths) for g in spec.any_glob))
    if all(criteria):
        return Detection.MATCHED
    # A truncated tree can prove presence, never absence.
    return Detection.UNDETERMINED if tree.truncated else Detection.NOT_MATCHED


def _negate(*, states: list[Detection]) -> Detection:
    if Detection.MATCHED in states:
        return Detection.NOT_MATCHED
    if Detection.UNDETERMINED in states:
        return Detection.UNDETERMINED
    return Detection.MATCHED
