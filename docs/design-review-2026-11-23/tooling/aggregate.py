import json, glob, collections
R = {}
for f in glob.glob("/tmp/claude-0/shots/results-w*.json") + glob.glob("/tmp/claude-0/shots/results-states.json"):
    R.update(json.load(open(f)))
axe_tot = collections.Counter(); axe_pages = collections.defaultdict(set)
rows = []
for k, v in sorted(R.items()):
    if "axe" not in v:
        rows.append((k, "CHECK ERROR", v.get("checkError", "")[:80])); continue
    for a in v["axe"]:
        axe_tot[(a["id"], a["impact"])] += a["n"]; axe_pages[a["id"]].add(k.split("__")[0])
    rows.append((k, v["overflow"], v["smallCount"], v["cls"], ",".join(v["snake"][:8]), ",".join(v["codes"][:6]), ";".join(f'{a["id"]}:{a["n"]}' for a in v["axe"]), "|".join(v["errs"])[:100]))
print("AXE TOTALS")
for (i, imp), n in axe_tot.most_common(): print(f"  {i} [{imp}] nodes={n} pages={len(axe_pages[i])}")
print("\nkey | overflow | small<44 | CLS | snake_case | codes | axe | errors")
for r in rows: print(" | ".join(map(str, r)))
