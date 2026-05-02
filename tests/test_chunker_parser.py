import typing
from pathlib import Path

from safecpp_reviewer.chunker.parser import CppParser, Parser


def test_cpp_parser_chunks_sample_violations_fixture() -> None:
    source_file: typing.Final = Path("tests/fixtures/sample_violations.cpp")

    chunks: typing.Final = CppParser().parse_file(source_file)
    by_name: typing.Final = {chunk.name: chunk for chunk in chunks if chunk.name is not None}

    assert Parser is CppParser
    assert chunks[0].chunk_type == "global"
    assert (
        by_name["process"].chunk_type,
        by_name["process"].start_line,
        by_name["process"].end_line,
    ) == (
        "function",
        10,
        18,
    )
    assert (
        by_name["magic"].chunk_type,
        by_name["magic"].start_line,
        by_name["magic"].end_line,
    ) == (
        "function",
        21,
        23,
    )
    assert (by_name["Base"].chunk_type, by_name["Base"].start_line, by_name["Base"].end_line) == (
        "class",
        26,
        30,
    )
    assert (
        by_name["Derived"].chunk_type,
        by_name["Derived"].start_line,
        by_name["Derived"].end_line,
    ) == (
        "class",
        32,
        35,
    )
    assert (
        by_name["unused_helper"].chunk_type,
        by_name["unused_helper"].start_line,
        by_name["unused_helper"].end_line,
    ) == (
        "function",
        38,
        41,
    )
    assert by_name["process"].token_estimate == len(by_name["process"].content) // 4


def test_cpp_parser_parse_file(tmp_path: Path) -> None:
    source_file: typing.Final = tmp_path / "sample.cpp"
    source_file.write_text("int main() { return 0; }\n", encoding="utf-8")

    chunks: typing.Final = CppParser().parse_file(source_file)

    assert len(chunks) == 1
    assert chunks[0].file == source_file
    assert chunks[0].name == "main"
    assert chunks[0].chunk_type == "function"


def test_cpp_parser_keeps_out_of_class_method_scope() -> None:
    source: typing.Final = "class Motor { void tick(); };\nvoid Motor::tick() {}\n"

    chunks: typing.Final = CppParser().parse_source(source, "motor.cpp")
    function_names: typing.Final = [
        chunk.name for chunk in chunks if chunk.chunk_type == "function"
    ]

    assert function_names == ["Motor::tick"]
