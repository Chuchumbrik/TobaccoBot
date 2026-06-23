from oshisha.llm_json import extract_json_array, extract_json_object


def test_extract_json_object_from_fenced():
    raw = '```json\n{"type": "search", "queries": ["a"]}\n```'
    data = extract_json_object(raw)
    assert data == {"type": "search", "queries": ["a"]}


def test_extract_json_array_balanced():
    raw = 'Here: [{"name": "X", "components": ["a"]}] end'
    data = extract_json_array(raw)
    assert isinstance(data, list) and data[0]["name"] == "X"
