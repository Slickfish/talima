"""CortexFingerprinter (arch §7.3) — compact mathematical signature of the
graph topology at a point in time. Comparable across archive snapshots.

Six-dimensional signature:
  [n_nodes, n_edges, mean_degree, clustering_coefficient,
   avg_path_length_proxy, structural_entropy]

The Euclidean-distance metric is normalised so two identical fingerprints
return 0.0 and any difference is bounded by 1.0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import networkx as nx
import numpy as np

if TYPE_CHECKING:
    from backend.memory.hippocampus import Hippocampus


SIGNATURE_DIM = 6

# Fix B — drift policy thresholds. Distance between consecutive snapshot
# fingerprints; severity escalates to "alert" at 0.45 regardless of how many
# warns preceded it.
DRIFT_WARN_THRESHOLD  = 0.25
DRIFT_ALERT_THRESHOLD = 0.45


@dataclass(frozen=True)
class DriftAssessment:
    distance: float
    severity: str              # "stable" | "warn" | "alert"
    consecutive_warns: int
    recommended_action: str    # "none" | "monitor" | "consolidation_recommended"
                                #  | "consolidation_pass_recommended"

    def to_dict(self) -> dict:
        return {
            "distance":           round(self.distance, 4),
            "severity":           self.severity,
            "consecutive_warns":  self.consecutive_warns,
            "recommended_action": self.recommended_action,
        }


def assess_drift(
    fp_prev: "CortexFingerprint",
    fp_curr: "CortexFingerprint",
    consecutive_warns: int = 0,
) -> DriftAssessment:
    """Compare two fingerprints and recommend an action.

    - severity "stable" when distance < DRIFT_WARN_THRESHOLD; resets warn run
    - severity "warn"   when DRIFT_WARN <= distance < DRIFT_ALERT
        - first warn → action "monitor"
        - second consecutive warn → action "consolidation_recommended"
    - severity "alert"  when distance >= DRIFT_ALERT_THRESHOLD
        - always action "consolidation_pass_recommended" (immediate)
    """
    d = fp_prev.distance(fp_curr)
    if d >= DRIFT_ALERT_THRESHOLD:
        return DriftAssessment(
            distance=d, severity="alert",
            consecutive_warns=consecutive_warns,
            recommended_action="consolidation_pass_recommended",
        )
    if d >= DRIFT_WARN_THRESHOLD:
        new_warns = consecutive_warns + 1
        action = "consolidation_recommended" if new_warns >= 2 else "monitor"
        return DriftAssessment(
            distance=d, severity="warn",
            consecutive_warns=new_warns,
            recommended_action=action,
        )
    return DriftAssessment(
        distance=d, severity="stable",
        consecutive_warns=0,
        recommended_action="none",
    )


@dataclass(frozen=True)
class CortexFingerprint:
    """Snapshot signature. Field names follow arch §7.3 naming."""
    n_nodes:                 int
    n_edges:                 int
    mean_degree:             float
    clustering_coefficient:  float
    avg_path_length:         float
    structural_entropy:      float
    top_hub_nodes:           tuple = ()
    cross_domain_bridges:    int   = 0

    @property
    def signature(self) -> list[float]:
        return [
            float(self.n_nodes),
            float(self.n_edges),
            float(self.mean_degree),
            float(self.clustering_coefficient),
            float(self.avg_path_length),
            float(self.structural_entropy),
        ]

    def to_dict(self) -> dict:
        return {
            "n_nodes":                self.n_nodes,
            "n_edges":                self.n_edges,
            "mean_degree":            round(self.mean_degree, 4),
            "clustering_coefficient": round(self.clustering_coefficient, 4),
            "avg_path_length":        round(self.avg_path_length, 4),
            "structural_entropy":     round(self.structural_entropy, 4),
            "top_hub_nodes":          list(self.top_hub_nodes),
            "cross_domain_bridges":   self.cross_domain_bridges,
            "signature":              [round(x, 6) for x in self.signature],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CortexFingerprint":
        """Reconstruct from a to_dict() payload. The `signature` field is
        derived from the others, so it's recomputed implicitly via the property."""
        return cls(
            n_nodes                = int(d.get("n_nodes", 0)),
            n_edges                = int(d.get("n_edges", 0)),
            mean_degree            = float(d.get("mean_degree", 0.0)),
            clustering_coefficient = float(d.get("clustering_coefficient", 0.0)),
            avg_path_length        = float(d.get("avg_path_length", 0.0)),
            structural_entropy     = float(d.get("structural_entropy", 0.0)),
            top_hub_nodes          = tuple(d.get("top_hub_nodes") or ()),
            cross_domain_bridges   = int(d.get("cross_domain_bridges", 0)),
        )

    def distance(self, other: "CortexFingerprint") -> float:
        """Instance-method form of the distance metric — delegates to the
        static method on CortexFingerprinter so callers can write
        `fp_prev.distance(fp_curr)` (Fix B's drift assessment uses this shape)."""
        return CortexFingerprinter.distance(self, other)


class CortexFingerprinter:
    """Computes a CortexFingerprint from a Hippocampus snapshot."""

    @staticmethod
    def compute(hippocampus: "Hippocampus") -> CortexFingerprint:
        g: nx.DiGraph = hippocampus.graph
        n_nodes = g.number_of_nodes()
        n_edges = g.number_of_edges()
        mean_deg = (2.0 * n_edges / n_nodes) if n_nodes else 0.0

        # Clustering on the undirected projection — directed clustering is
        # noisier and arch §7.3 uses "clustering_coefficient" as a single scalar.
        try:
            clustering = nx.average_clustering(g.to_undirected())
        except Exception:
            clustering = 0.0

        # Average path length via NumPy on the largest weakly connected
        # component. Disconnected graphs use the largest component's diameter
        # as a finite proxy.
        try:
            if n_nodes <= 1:
                avg_path = 0.0
            else:
                wccs = list(nx.weakly_connected_components(g))
                largest = max(wccs, key=len) if wccs else set()
                if len(largest) <= 1:
                    avg_path = 0.0
                else:
                    sub = g.subgraph(largest).to_undirected()
                    avg_path = nx.average_shortest_path_length(sub)
        except Exception:
            avg_path = 0.0

        # Structural entropy — Shannon entropy of the degree distribution,
        # normalised to [0, 1] by log(n_nodes) (a uniform graph maxes out).
        if n_nodes:
            degrees = np.array([d for _, d in g.degree()], dtype=np.float64)
            total = float(degrees.sum()) or 1.0
            probs = degrees / total
            with np.errstate(divide="ignore", invalid="ignore"):
                entropy = -np.nansum(np.where(probs > 0, probs * np.log(probs), 0.0))
            normaliser = math.log(n_nodes) if n_nodes > 1 else 1.0
            structural_entropy = float(entropy / normaliser) if normaliser else 0.0
        else:
            structural_entropy = 0.0

        # Top hubs by total degree
        top_hubs = tuple(
            nid for nid, _ in sorted(g.degree(), key=lambda kv: -kv[1])[:5]
        )

        # Cross-domain bridges — edges connecting nodes whose `column`
        # attributes differ (Phase 3 columns).
        cross_bridges = 0
        for s, t in g.edges():
            try:
                cs = g.nodes[s]["node"].column
                ct = g.nodes[t]["node"].column
            except Exception:
                continue
            if cs and ct and cs != ct:
                cross_bridges += 1

        return CortexFingerprint(
            n_nodes=n_nodes,
            n_edges=n_edges,
            mean_degree=mean_deg,
            clustering_coefficient=clustering,
            avg_path_length=avg_path,
            structural_entropy=structural_entropy,
            top_hub_nodes=top_hubs,
            cross_domain_bridges=cross_bridges,
        )

    @staticmethod
    def distance(fp_a: CortexFingerprint, fp_b: CortexFingerprint) -> float:
        """Normalised Euclidean distance in [0, 1].

        0.0 = identical signatures (impossible after first month of use).
        1.0 = maximum structural difference.
        """
        a = np.array(fp_a.signature, dtype=np.float64)
        b = np.array(fp_b.signature, dtype=np.float64)
        diff_norm = float(np.linalg.norm(a - b))
        sum_norm  = float(np.linalg.norm(a) + np.linalg.norm(b))
        if sum_norm == 0.0:
            return 0.0
        return min(1.0, diff_norm / sum_norm)
