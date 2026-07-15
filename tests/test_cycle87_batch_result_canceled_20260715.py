from manju.build.graph import BatchResult

def test_batch_result_canceled_in_to_dict():
    br = BatchResult(canceled=True, errors=["已取消"])
    d = br.to_dict()
    assert d.get("canceled") is True
    assert d.get("errors") == ["已取消"]
