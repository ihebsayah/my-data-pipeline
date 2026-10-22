import runpy
import zipfile


def test_independent_disk_backed_counter(tmp_path):
    archive = tmp_path / "audit.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.csv", "ride_id,value\nA,1\nA,2\n,3\n")
        z.writestr("b.csv", "ride_id,value\nA,4\nB,5\n")
    audit = runpy.run_path("scripts/reconcile_source.py")["audit"]
    counts = audit(archive, tmp_path / "ids.sqlite")
    assert (counts["rows"], counts["unique_ride_ids"],
            counts["duplicate_ride_ids"], counts["null_ride_ids"]) == (5, 2, 2, 1)
    assert [m["unique_ride_ids"] for m in counts["members"]] == [1, 2]
    assert [m["duplicate_ride_ids"] for m in counts["members"]] == [1, 0]
