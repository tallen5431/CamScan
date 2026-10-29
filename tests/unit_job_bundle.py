"""The 3D part travels with a submission.

A job file (💾 Save job) and a submission used to carry every photo but not the 3D part
assembled from them — which outline was the Top / Side / End, the wall, the typed sizes —
so a job opened by Datum from a submission had to be assembled again. The browser now sends
`part3d`; the server writes it into the job.camscan.json it keeps (so Load job opens the
part) and says what it is in the summary. Junk in that field must not cost the submission.

    python tests/unit_job_bundle.py
"""
import base64
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def _view(label, key):
    import cv2
    import numpy as np
    img = np.full((60, 80, 3), 180, np.uint8)
    raw = "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()
    return {"label": label, "image": raw, "photoKey": key, "measurements": [],
            "restore": {"v": 1, "w": 80, "h": 60, "raw": raw, "calib": {"markers": []}, "ann": [],
                        "manual": {"mmPerPx": 0.5}}}


def main():
    import app as appmod
    cli = appmod.server.test_client()
    part = {"views": {"top": {"points": [[0, 0], [700, 0], [700, 175], [0, 175]], "mm_per_px": 1,
                              "source": "photo-a", "ann_id": 3, "rot": 1},
                      "end": {"points": [[0, 0], [175, 0], [175, 50], [0, 50]], "mm_per_px": 1,
                              "source": "photo-b"}},
            "opts": {"wall": 3, "known": {"length": 700}, "section": {"radius_mm": 12}}}
    old = appmod.SUBMISSIONS_DIR
    with tempfile.TemporaryDirectory() as td:
        appmod.SUBMISSIONS_DIR = td
        try:
            r = cli.post("/api/submit", json={"id": "job-u", "brief": {"part": "step pad"},
                                              "views": [_view("Top", "photo-a"), _view("End", "photo-b")],
                                              "part3d": part})
            junk = cli.post("/api/submit", json={"id": "job-j", "views": [_view("Top", "photo-c")],
                                                 "part3d": ["not", "a", "part"]})
            dirs = sorted(os.listdir(td))
            got = {}
            for d in dirs:
                p = os.path.join(td, d)
                b = json.load(open(os.path.join(p, "job.camscan.json")))
                got[d.split("-job-")[-1]] = (b, open(os.path.join(p, "summary.txt")).read())
        finally:
            appmod.SUBMISSIONS_DIR = old
    b, summary = got.get("u", ({}, ""))
    check("a submission's job file carries the 3D part as sent, and each photo's key",
          r.status_code == 200 and b.get("part3d") == part
          and [v.get("photoKey") for v in b.get("views", [])] == ["photo-a", "photo-b"],
          f"status {r.status_code}, part3d {'same' if b.get('part3d') == part else b.get('part3d')}")
    check("the summary names the 3D part's views and wall",
          "3D part: Top, End — wall 3 mm" in summary, summary.splitlines()[-1] if summary else "no summary")
    jb, jsum = got.get("j", ({}, ""))
    check("a junk part3d is left out, and the submission still goes through",
          junk.status_code == 200 and "part3d" not in jb and "3D part" not in jsum,
          f"status {junk.status_code}, keys {sorted(jb)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # a crash is a failure, not a skip
        import traceback
        traceback.print_exc()
        check("unit_job_bundle ran to the end", False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    sys.exit(1 if _failures else 0)
