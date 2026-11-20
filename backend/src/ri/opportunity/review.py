"""Counterevidence review for priority >= high (spec 07 §8). EVIDENCE-ONLY inputs; can downgrade/reopen research,
never silently elevate. A model reviewer (optional) is merged conservatively: contradictions only ever add."""
from __future__ import annotations

from typing import Any, Protocol

from .model import Facts


class ModelReviewer(Protocol):
    def review(self, evidence_pack: dict[str, Any]) -> dict[str, Any]: ...


def review(f: Facts, *, model_reviewer: ModelReviewer | None = None) -> dict[str, Any]:
    supports: list[dict[str, Any]] = []
    contradicts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    route_change: str | None = None
    ev_ids = [str(c) for s in f.signals for c in (s["evidence_claim_ids"] or [])]

    def add(bucket, q, detail, claims=None):
        bucket.append({"question": q, "detail": detail, "claim_ids": claims if claims is not None else ev_ids[:5]})

    # 1 correct entity?
    if f.open_identity_review:
        add(unknowns, "Correct entity?", "identity review still open for this account")
    elif f.org["verification_status"] == "unverified":
        add(unknowns, "Correct entity?", "legal identity not registry-verified; matched by sheet name/domain only")
    else:
        add(supports, "Correct entity?", "legal identity verified")
    # 2 dates / programme still current?
    st = f.initiative["stage"]
    if st in {"completed", "cancelled", "paused"}:
        add(contradicts, "Is the programme still active?", f"initiative is {st}")
    elif f.initiative["last_material_event_at"] and (f.now - f.initiative["last_material_event_at"]).days > 180:
        add(unknowns, "Is the programme still active?", "no material update for >180 days")
    else:
        add(supports, "Is the programme still active?", f"stage {st} per cited events")
    # 3 incumbent coverage
    prime = [e for e in f.edges if e["supplier_role"] in {"si", "prime"} and "development" in (e["scope_text"] or "")]
    if prime and f.hyp["route_type"] == "direct":
        add(contradicts, "Does an incumbent cover the scope?", f"{prime[0]['supplier_name']} holds stated development scope; direct-bank pitch is weak")
        route_change = "prime_subcontract"
    elif f.edges:
        add(unknowns, "Does an incumbent cover the scope?", "suppliers named but scope allocation is not stated")
    else:
        add(unknowns, "Does an incumbent cover the scope?", "no supplier information found")
    # 4 independent corroboration
    groups = {g for s in f.signals for g in (s["independent_origin_groups"] or [])}
    (add(supports, "Independently corroborated?", f"{len(groups)} independent origin groups") if len(groups) >= 2 else
     add(unknowns, "Independently corroborated?", f"{len(groups)} independent origin group(s); repeated coverage of one release is not corroboration"))
    # 5 external demand
    ds = f.hyp["external_demand_status"]
    (add(supports, "Is external demand established?", f"demand {ds}") if ds in {"explicit", "buyer_confirmed"} else
     add(unknowns, "Is external demand established?", "public initiative evidence only; outsourcing not established"))
    # 6 direct vs partner purchasing
    if f.route and f.route["access_status"] in {"restricted", "blocked"}:
        add(contradicts, "Is this the right purchasing route?", f"route {f.route['access_status']}: {f.route['restriction']}")
    else:
        add(unknowns, "Is this the right purchasing route?", "supplier eligibility/purchasing route unverified")
    # 7 buyer responsible?
    (add(supports, "Is the buyer actually responsible?", "person verified for this account") if any(c["verification_status"] == "verified" for c in f.contacts) else
     add(unknowns, "Is the buyer actually responsible?", "role identified at best; initiative responsibility not verified; budget authority not inferred from title"))
    # 8 budget inferred?
    (add(supports, "Is budget evidenced?", "budget verified") if f.initiative["budget_verified"] else
     add(unknowns, "Is budget evidenced?", "budget_verified=false: no cited source"))
    # 9 client proof permitted?
    add(unknowns, "Is client proof approved for external use?", "no approved proof asset linked yet (restricted references are withheld from drafts)")
    # 10 what would falsify
    falsifiers = ["Programme completed/cancelled/paused in a newer official source", "Prime/vendor contract explicitly covers all engineering scope",
                  "Buyer states no external engineering spend or closed supplier list", "Account owned by another party or contact opted out"]
    out = {"supports": supports, "contradicts": contradicts, "unknowns": unknowns, "proposed_route_change": route_change,
           "confidence_impact": "downgrade" if contradicts else ("none" if len(unknowns) <= 4 else "cap_at_medium"), "falsifiers": falsifiers}
    if model_reviewer is not None:
        pack = {"supports": supports, "contradicts": contradicts, "unknowns": unknowns}      # evidence only: no CRM notes, no PII
        extra = model_reviewer.review(pack)
        out["contradicts"] += [x for x in extra.get("contradicts", []) if isinstance(x, dict) and x.get("claim_ids")]      # needs citations
        out["unknowns"] += [x for x in extra.get("unknowns", []) if isinstance(x, dict)]
        # the model can NOT add 'supports' or raise confidence
        if out["contradicts"]:
            out["confidence_impact"] = "downgrade"
    return out
