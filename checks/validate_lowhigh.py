import json, csv, pathlib
BASE = pathlib.Path(__file__).resolve().parents[1]
load = lambda p: json.loads((BASE/p).read_text(encoding="utf8"))
s = load("data/scenarios_R0_R4.json"); g = load("gold/gold_per_run.json"); ref = load("gold/reference_conditions_and_logic.json")
sw = load("gold/STEPWISE_GOLD_REVIEW.json")
SG = g["scenario_statuses"]
IDS = {"G_C1","G_C2","G_C3","G_X1","G_X2","G_X3","G_X4","G_X5"}
BASIS = {"explicit","entailed","unknown","conflicting"}; REL = {"required_for_yes","decisive","irrelevant_to_outcome"}
WORLDS = ["R1_L","R1_H","R2_L","R2_H","R3_L","R3_H"]
assert set(SG) == set(WORLDS) and "R4" not in json.dumps(g) and "G_C4" not in json.dumps(g)
text = {w["id"]: w["additional_information"] for w in s["worlds"]}

def evaluate(expr, st):
    if "ref" in expr: return st[expr["ref"]]
    if expr["op"] == "NOT": return {"TRUE":"FALSE","FALSE":"TRUE","UNKNOWN":"UNKNOWN","CONFLICTING":"UNKNOWN"}[evaluate(expr["arg"], st)]
    v = [evaluate(c, st) for c in expr["args"]]
    return "FALSE" if "FALSE" in v else "TRUE" if all(x == "TRUE" for x in v) else "UNKNOWN"

for w, d in SG.items():
    for k in ("statuses","evidence_basis","evidence_source_form","outcome_relevance","evidence_quotes","evidence_notes"):
        assert set(d[k]) == IDS, (w, k)
    assert "evidence_type" not in d, w
    for a in IDS:
        st, b, q = d["statuses"][a], d["evidence_basis"][a], d["evidence_quotes"][a]
        assert b in BASIS and d["outcome_relevance"][a] in REL, (w, a)
        assert (b == "unknown") == (st == "UNKNOWN"), (w, a)
        assert b == "unknown" or q, (w, a, "needs a verbatim quote")
        for x in q: assert x in text[w], (w, a, x)
    derived = {"TRUE":"YES","FALSE":"NO","UNKNOWN":"NEED_MORE_INFORMATION"}[evaluate(ref["reference_logic"], d["statuses"])]
    assert derived == d["reference_decision"], (w, derived)
    for r in sw[w]["A2_reference_statuses"]:
        assert r["expected_status"] == d["statuses"][r["id"]] and r["verbatim_evidence"] == d["evidence_quotes"][r["id"]], (w, r["id"])
for p in ("R1","R2","R3"):
    assert SG[p+"_L"]["statuses"] == SG[p+"_H"]["statuses"], p
    assert SG[p+"_L"]["evidence_quotes"] != SG[p+"_H"]["evidence_quotes"] or p == "R2", p

rows = list(csv.DictReader((BASE/"review/condition_evidence_crosswalk.csv").open(encoding="utf8")))
assert len(rows) == 48
for r in rows:
    d = SG[r["scenario"]]; a = r["condition_id"]
    assert r["reference_status"] == d["statuses"][a] and r["evidence_basis"] == d["evidence_basis"][a]
    assert r["source_form"] == d["evidence_source_form"][a] and r["outcome_relevance"] == d["outcome_relevance"][a]
    assert r["verbatim_excerpt"] == " || ".join(d["evidence_quotes"][a])

# length control: High variants must be close in length so length is not an outcome cue
hw = {w: len(text[w].split()) for w in ("R1_H","R2_H","R3_H")}
assert all(240 <= n <= 310 for n in hw.values()), hw
assert max(hw.values()) - min(hw.values()) <= 40, hw
assert all(len(text[w].split()) < len(text[w[:2]+"_H"].split()) for w in ("R1_L","R2_L","R3_L"))
# regression guards for audited defects
assert "16 March 2026" in text["R1_L"] and "16 March 2026" in text["R1_H"] and "well below" in text["R1_H"]
assert "only benefit recorded" not in text["R3_H"] and "cannot establish" not in text["R3_L"]
assert "payroll giving" in text["R1_H"].lower() and "CC-0314" in text["R1_H"]
for w in WORLDS: assert "?" not in text[w], w
print("PASS: Gold structure, verbatim quotes, JSON<->CSV<->stepwise identity, L/H state equality, derived decisions, High-variant length control, regression guards.")
print("LIMITATION: these checks do NOT verify legal or semantic Gold correctness.")
