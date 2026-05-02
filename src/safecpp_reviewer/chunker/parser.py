from __future__ import annotations

import typing
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

import tree_sitter_cpp
from tree_sitter import Language, Node
from tree_sitter import Parser as TreeSitterParser

from safecpp_reviewer.chunker.models import Chunk

ChunkType = Literal["function", "class", "namespace", "global", "raw"]


class CppParser:
    """Parse C++ source files into semantic chunks using Tree-sitter."""

    _CLASS_NODE_TYPES = {"class_specifier", "struct_specifier", "union_specifier"}
    _CHUNK_NODE_TYPES: dict[str, ChunkType] = {
        "function_definition": "function",
        "class_specifier": "class",
        "struct_specifier": "class",
        "union_specifier": "class",
        "namespace_definition": "namespace",
    }
    _NAME_NODE_TYPES = {
        "identifier",
        "field_identifier",
        "namespace_identifier",
        "qualified_identifier",
        "scoped_identifier",
        "template_function",
        "type_identifier",
        "destructor_name",
        "operator_name",
    }

    def __init__(self) -> None:
        self.language = Language(tree_sitter_cpp.language())
        self.parser = TreeSitterParser(self.language)

    def parse_file(self, path: str | Path) -> list[Chunk]:
        """Parse a C++ source/header file into chunks."""
        source_path: typing.Final = Path(path)
        source: typing.Final = source_path.read_text(encoding="utf-8")
        return self.parse_source(source, source_path)

    def parse_source(self, source: str, file: str | Path = "<memory>") -> list[Chunk]:
        """Parse C++ source text into chunks."""
        source_path: typing.Final = Path(file)
        source_bytes: typing.Final = source.encode("utf-8")
        tree: typing.Final = self.parser.parse(source_bytes)
        root: typing.Final = tree.root_node

        chunks: typing.Final = [
            self._chunk_from_node(node, source_bytes, source_path)
            for node in self._iter_chunk_nodes(root)
        ]
        chunks.extend(self._global_chunks(root, source, source_path, chunks))

        if not chunks:
            return [self._raw_chunk(source, source_path)]

        return sorted(
            chunks, key=lambda chunk: (chunk.start_line, chunk.end_line, chunk.chunk_type)
        )

    def _iter_chunk_nodes(self, root: Node) -> Iterable[Node]:
        stack: typing.Final = list(reversed(root.children))
        while stack:
            node = stack.pop()
            if node.type in self._CHUNK_NODE_TYPES:
                yield node
            stack.extend(reversed(node.children))

    def _chunk_from_node(self, node: Node, source_bytes: bytes, file: Path) -> Chunk:
        content: typing.Final = self._node_text(node, source_bytes)
        return Chunk(
            file=file,
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
            content=content,
            chunk_type=self._CHUNK_NODE_TYPES[node.type],
            name=self._qualified_name(node) or None,
            token_estimate=self._estimate_tokens(content),
        )

    def _global_chunks(
        self,
        root: Node,
        source: str,
        file: Path,
        semantic_chunks: list[Chunk],
    ) -> list[Chunk]:
        if not source.strip():
            return []

        occupied: typing.Final = [
            (chunk.start_line, chunk.end_line)
            for chunk in semantic_chunks
            if chunk.chunk_type in {"function", "class", "namespace"}
        ]
        if not occupied:
            return []

        chunks: typing.Final[list[Chunk]] = []
        lines: typing.Final = source.splitlines(keepends=True)
        line_count: typing.Final = len(lines) or 1
        cursor = 1

        for start, end in sorted(occupied):
            if cursor < start:
                chunks.extend(self._global_chunk_for_range(lines, cursor, start - 1, file))
            cursor = max(cursor, end + 1)

        if cursor <= line_count:
            chunks.extend(self._global_chunk_for_range(lines, cursor, line_count, file))

        if root.has_error and not chunks and not semantic_chunks:
            return [self._raw_chunk(source, file)]

        return chunks

    def _global_chunk_for_range(
        self,
        lines: list[str],
        start_line: int,
        end_line: int,
        file: Path,
    ) -> list[Chunk]:
        content: typing.Final = "".join(lines[start_line - 1 : end_line])
        if not content.strip():
            return []

        return [
            Chunk(
                file=file,
                start_line=start_line,
                end_line=end_line,
                content=content,
                chunk_type="global",
                name=None,
                token_estimate=self._estimate_tokens(content),
            )
        ]

    def _raw_chunk(self, source: str, file: Path) -> Chunk:
        return Chunk(
            file=file,
            start_line=1,
            end_line=max(1, len(source.splitlines())),
            content=source,
            chunk_type="raw",
            name=None,
            token_estimate=self._estimate_tokens(source),
        )

    def _qualified_name(self, node: Node) -> str:
        own_name: typing.Final = self._node_name(node)
        if own_name is None:
            return ""

        scopes: typing.Final[list[str]] = []
        parent = node.parent
        while parent is not None:
            if parent.type in {"namespace_definition", *self._CLASS_NODE_TYPES}:
                scope_name = self._node_name(parent)
                if scope_name:
                    scopes.append(scope_name)
            parent = parent.parent

        return "::".join([*reversed(scopes), own_name])

    def _node_name(self, node: Node) -> str | None:
        name: typing.Final = node.child_by_field_name("name")
        if name is not None:
            return self._node_bytes(name).decode("utf-8", errors="replace")

        declarator: typing.Final = node.child_by_field_name("declarator")
        if declarator is not None:
            return self._identifier_from_declarator(declarator)

        return self._find_identifier_child(node)

    def _identifier_from_declarator(self, node: Node | None) -> str | None:
        if node is None:
            return None

        if node.type in self._NAME_NODE_TYPES:
            return self._node_bytes(node).decode("utf-8", errors="replace")

        name: typing.Final = node.child_by_field_name("name")
        if name is not None:
            return self._node_bytes(name).decode("utf-8", errors="replace")

        declarator: typing.Final = node.child_by_field_name("declarator")
        if declarator is not None:
            nested: typing.Final = self._identifier_from_declarator(declarator)
            if nested is not None:
                return nested

        for child in node.children:
            child_name = self._identifier_from_declarator(child)
            if child_name is not None:
                return child_name

        return None

    def _find_identifier_child(self, node: Node) -> str | None:
        stack: typing.Final = list(reversed(node.children))
        while stack:
            child = stack.pop()
            if child.type in self._NAME_NODE_TYPES:
                return self._node_bytes(child).decode("utf-8", errors="replace")
            stack.extend(reversed(child.children))

        return None

    def _node_text(self, node: Node, source_bytes: bytes) -> str:
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _node_bytes(self, node: Node) -> bytes:
        return node.text or b""

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)


Parser = CppParser


def parse_chunks(source_file: str | Path) -> list[Chunk]:
    """Parse a C++ file into function/class/namespace chunks."""
    return CppParser().parse_file(source_file)
