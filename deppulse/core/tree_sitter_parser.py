"""TreeSitterParser: abstract base class for tree-sitter-based language scanners.

Each language-specific scanner (Kotlin, C++, JavaScript, TypeScript) inherits
from this class and implements the extraction methods. The orchestrator uses
these to build the Unified IR.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tree_sitter import Language, Tree

from deppulse.core.ir import (
    ImportEdge,
    ImportKind,
    LineRange,
    RawCall,
    RawImport,
    RawSymbol,
    SymType,
    Visibility,
)


class TreeSitterParser(ABC):
    """
    Abstract base class for tree-sitter-based language parsers.

    Subclasses must implement language-specific extraction methods.
    The orchestrator calls these to build the Unified IR.

    Usage
    -----
    ```python
    class KotlinTreeSitterParser(TreeSitterParser):
        language_name = "kotlin"
        language: Language  # set by __init__ using tree_sitter.Language

        def extract_imports(self, tree, file_path):
            ...  # return list[RawImport]
        def extract_symbols(self, tree, file_path):
            ...  # return list[RawSymbol]
        def extract_calls(self, tree, file_path):
            ...  # return list[RawCall]
    ```
    """

    language_name: str = "unknown"

    @property
    @abstractmethod
    def language(self) -> "Language":
        """Return the tree-sitter Language object for this parser."""

    # ------------------------------------------------------------------------
    # Core parsing
    # ------------------------------------------------------------------------

    def parse(self, source: bytes) -> "Tree":
        """
        Parse source bytes into a tree-sitter Tree.

        Uses the tree-sitter API compatible with both old and new versions:
        - New (v0.23+): Parser(language) constructor
        - Old (v0.20): parser.set_language(language)
        """
        from tree_sitter import Parser

        lang = self.language
        parser = Parser(lang)
        return parser.parse(source)

    def parse_file(self, file_path: Path) -> "Tree":
        """Parse a file from disk."""
        content = file_path.read_bytes()
        return self.parse(content)

    def query(self, tree: "Tree", pattern: str) -> list:
        """
        Run a tree-sitter query on a tree and return matched nodes.

        Parameters
        ----------
        tree : Tree
            The parsed tree-sitter Tree.
        pattern : str
            A tree-sitter query string.
            See https://tree-sitter.github.io/tree-sitter/using-parsers/queries/pattern-matching
        """
        return tree.root_node.children

    # ------------------------------------------------------------------------
    # Extraction methods (override in subclasses)
    # ------------------------------------------------------------------------

    @abstractmethod
    def extract_imports(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[RawImport]:
        """
        Extract all import/include directives from a parsed tree.

        Parameters
        ----------
        tree : Tree
            The parsed tree-sitter Tree.
        file_path : str
            Project-relative POSIX path of the source file.

        Returns
        -------
        list[RawImport]
            List of raw imports found in the file.
        """

    @abstractmethod
    def extract_symbols(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[RawSymbol]:
        """
        Extract all top-level symbol definitions from a parsed tree.

        Parameters
        ----------
        tree : Tree
            The parsed tree-sitter Tree.
        file_path : str
            Project-relative POSIX path of the source file.

        Returns
        -------
        list[RawSymbol]
            List of raw symbols found in the file.
        """

    def extract_calls(
        self,
        tree: "Tree",
        file_path: str,
    ) -> list[RawCall]:
        """
        Extract function/method call sites from a parsed tree.

        The base implementation returns an empty list.
        Subclasses can override to provide call site information.

        Parameters
        ----------
        tree : Tree
            The parsed tree-sitter Tree.
        file_path : str
            Project-relative POSIX path of the source file.

        Returns
        -------
        list[RawCall]
            List of call sites found in the file.
        """
        return []

    # ------------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------------

    def _node_text(self, node, source: bytes) -> str:
        """Get the text content of a tree-sitter node."""
        return source[node.byte_range[0]:node.byte_range[1]].decode("utf-8")

    def _node_line(self, node, source: bytes) -> int:
        """Get the 1-indexed line number for a node's start position."""
        return source[:node.byte_range[0]].count(b"\n") + 1

    def _node_column(self, node, source: bytes) -> int:
        """Get the 0-indexed column number for a node's start position."""
        text_before = source[:node.byte_range[0]]
        last_newline = text_before.rfind(b"\n")
        if last_newline == -1:
            return node.byte_range[0]
        return node.byte_range[0] - last_newline - 1

    def _node_end_line(self, node, source: bytes) -> int:
        """Get the 1-indexed end line number for a node."""
        return source[:node.byte_range[1]].count(b"\n") + 1

    def _node_range(self, node, source: bytes) -> LineRange:
        """Get the line range for a node."""
        return LineRange(
            start=self._node_line(node, source),
            end=self._node_end_line(node, source),
        )

    def _find_child_by_type(self, node, child_type: str):
        """Find the first child of a given type."""
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    def _find_children_by_type(self, node, child_type: str) -> list:
        """Find all children of a given type."""
        return [c for c in node.children if c.type == child_type]

    def _get_fqn(self, file_path: str, parent: Optional[str], name: str) -> str:
        """Build a fully-qualified name for a symbol."""
        if parent:
            return f"{parent}.{name}"
        return name

    # ------------------------------------------------------------------------
    # SymType / Visibility helpers
    # ------------------------------------------------------------------------

    def _sym_type_from_string(self, type_str: str) -> SymType:
        """Convert a type string to SymType."""
        mapping = {
            "function": SymType.FUNCTION,
            "class": SymType.CLASS,
            "method": SymType.METHOD,
            "property": SymType.PROPERTY,
            "constructor": SymType.CONSTRUCTOR,
            "interface": SymType.INTERFACE,
            "enum": SymType.ENUM,
            "annotation": SymType.ANNOTATION,
            "type_alias": SymType.TYPE_ALIAS,
        }
        return mapping.get(type_str, SymType.UNKNOWN)

    def _visibility_from_node(self, modifiers: list[str]) -> Visibility:
        """Determine visibility from modifier keywords."""
        if "public" in modifiers:
            return Visibility.PUBLIC
        if "private" in modifiers:
            return Visibility.PRIVATE
        if "protected" in modifiers:
            return Visibility.PROTECTED
        if "internal" in modifiers:
            return Visibility.INTERNAL
        return Visibility.UNKNOWN

    # ------------------------------------------------------------------------
    # Common tree traversal
    # ------------------------------------------------------------------------

    def _walk_tree(self, node, source: bytes, callback):
        """
        Walk all nodes in a tree, calling callback(node, depth) for each.
        """
        def _walk(n, depth, callback):
            callback(n, depth)
            for child in n.children:
                _walk(child, depth + 1, callback)

        _walk(node, 0, callback)

    def _all_nodes_of_type(self, tree: "Tree", node_type: str) -> list:
        """Return all nodes of a given type in a tree."""
        results = []
        def collect(n, depth):
            if n.type == node_type:
                results.append(n)
        self._walk_tree(tree.root_node, b"", collect)
        return results
