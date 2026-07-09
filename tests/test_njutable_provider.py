from data_source.njutable_provider import is_status_allowed, map_row_to_student
from config import load_settings


class DummyConfig(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_map_row_excludes_sensitive():
    settings = load_settings(DummyConfig())
    row = {
        "_id": "row1",
        "姓名": "张三",
        "学号": "261220001",
        "考生状态": "对外公布",
        "身份证号": "secret",
        "联系手机": "13800000000",
    }
    student = map_row_to_student(row, settings)
    assert student is not None
    assert student.name == "张三"
    data = student.to_dict()
    assert "身份证号" not in data
    assert "联系手机" not in data


def test_status_filter():
    assert is_status_allowed("对外公布", ("对外公布",))
    assert not is_status_allowed("有问题", ("对外公布", "有问题"))
    assert not is_status_allowed("", ("对外公布",))


def test_checked_status_allowed_when_in_allowlist():
    assert is_status_allowed("已校对", ("已校对", "对外公布"))
