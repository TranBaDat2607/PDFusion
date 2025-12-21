"""
Citation graph management for deep search.
Tracks relationships between papers and enables graph-based traversal.
"""

import logging
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, field

import networkx as nx

from .deep_search import PaperResult

logger = logging.getLogger(__name__)


@dataclass
class PaperNode:
    """Node in citation graph."""

    paper: PaperResult
    citations_out: List[str] = field(default_factory=list)  # Papers this cites
    citations_in: List[str] = field(default_factory=list)   # Papers that cite this
    relevance_scores: Dict[str, float] = field(default_factory=dict)
    visited: bool = False

    @property
    def paper_id(self) -> str:
        return self.paper.paper_id


class CitationGraph:
    """
    Manages citation relationships and graph traversal.
    Uses NetworkX for efficient graph operations.
    """

    def __init__(self):
        """Initialize citation graph."""
        self.graph = nx.DiGraph()  # Directed graph for citations
        self.nodes: Dict[str, PaperNode] = {}
        self.visited: Set[str] = set()

        logger.info("Citation graph initialized")

    def add_paper(self, paper: PaperResult) -> bool:
        """
        Add a paper to the graph.

        Args:
            paper: Paper to add

        Returns:
            True if added, False if already exists
        """
        if paper.paper_id in self.nodes:
            logger.debug(f"Paper already in graph: {paper.paper_id}")
            return False

        # Create node
        node = PaperNode(paper=paper)
        self.nodes[paper.paper_id] = node

        # Add to NetworkX graph
        self.graph.add_node(
            paper.paper_id,
            title=paper.title,
            year=paper.year,
            source=paper.source,
            relevance=paper.relevance_score
        )

        logger.debug(f"Added paper to graph: {paper.paper_id}")
        return True

    def add_citation(
        self,
        from_paper_id: str,
        to_paper_id: str,
        citation_type: str = "cites"
    ):
        """
        Add citation relationship between papers.

        Args:
            from_paper_id: Source paper ID
            to_paper_id: Target paper ID
            citation_type: Type of citation ("cites" or "cited_by")
        """
        # Ensure both papers exist in graph
        if from_paper_id not in self.nodes or to_paper_id not in self.nodes:
            logger.warning(f"Cannot add citation: paper not in graph")
            return

        # Add to node tracking
        from_node = self.nodes[from_paper_id]
        to_node = self.nodes[to_paper_id]

        if citation_type == "cites":
            if to_paper_id not in from_node.citations_out:
                from_node.citations_out.append(to_paper_id)
            if from_paper_id not in to_node.citations_in:
                to_node.citations_in.append(from_paper_id)
        elif citation_type == "cited_by":
            if to_paper_id not in from_node.citations_in:
                from_node.citations_in.append(to_paper_id)
            if from_paper_id not in to_node.citations_out:
                to_node.citations_out.append(from_paper_id)

        # Add edge to NetworkX graph
        self.graph.add_edge(from_paper_id, to_paper_id, type=citation_type)

        logger.debug(f"Added citation: {from_paper_id} -> {to_paper_id} ({citation_type})")

    def get_paper(self, paper_id: str) -> Optional[PaperNode]:
        """Get paper node by ID."""
        return self.nodes.get(paper_id)

    def get_unvisited_neighbors(
        self,
        paper_id: str,
        max_neighbors: int = 10,
        direction: str = "out"  # "out" for citations, "in" for cited-by
    ) -> List[PaperNode]:
        """
        Get unvisited neighboring papers.

        Args:
            paper_id: Source paper ID
            max_neighbors: Maximum neighbors to return
            direction: "out" for citations, "in" for cited-by

        Returns:
            List of unvisited neighbor nodes
        """
        if paper_id not in self.nodes:
            return []

        node = self.nodes[paper_id]

        # Get neighbor IDs based on direction
        if direction == "out":
            neighbor_ids = node.citations_out
        elif direction == "in":
            neighbor_ids = node.citations_in
        else:
            neighbor_ids = node.citations_out + node.citations_in

        # Filter unvisited
        unvisited = [
            self.nodes[nid]
            for nid in neighbor_ids
            if nid in self.nodes and nid not in self.visited
        ]

        # Sort by relevance score
        unvisited.sort(key=lambda n: n.paper.relevance_score, reverse=True)

        return unvisited[:max_neighbors]

    def mark_visited(self, paper_id: str):
        """Mark a paper as visited."""
        if paper_id in self.nodes:
            self.nodes[paper_id].visited = True
            self.visited.add(paper_id)

    def is_visited(self, paper_id: str) -> bool:
        """Check if paper has been visited."""
        return paper_id in self.visited

    def find_shortest_path(
        self,
        start_id: str,
        end_id: str
    ) -> Optional[List[str]]:
        """
        Find shortest path between two papers.

        Args:
            start_id: Start paper ID
            end_id: End paper ID

        Returns:
            List of paper IDs in path, or None if no path exists
        """
        if start_id not in self.nodes or end_id not in self.nodes:
            return None

        try:
            path = nx.shortest_path(self.graph, start_id, end_id)
            return path
        except nx.NetworkXNoPath:
            return None

    def get_most_relevant_unvisited(
        self,
        question: str,
        top_k: int = 5
    ) -> List[PaperNode]:
        """
        Get most relevant unvisited papers.

        Args:
            question: Research question (for relevance scoring)
            top_k: Number of papers to return

        Returns:
            List of most relevant unvisited papers
        """
        unvisited_nodes = [
            node for node in self.nodes.values()
            if not node.visited
        ]

        # Sort by relevance score
        unvisited_nodes.sort(
            key=lambda n: n.paper.relevance_score,
            reverse=True
        )

        return unvisited_nodes[:top_k]

    def get_connected_components(self) -> List[Set[str]]:
        """
        Get connected components in the graph.

        Returns:
            List of sets, each containing paper IDs in a component
        """
        # Convert directed to undirected for component analysis
        undirected = self.graph.to_undirected()
        components = list(nx.connected_components(undirected))
        return components

    def get_citation_depth(self, paper_id: str) -> Dict[str, int]:
        """
        Get citation depth from a starting paper.

        Args:
            paper_id: Starting paper ID

        Returns:
            Dict mapping paper IDs to their depth from start
        """
        if paper_id not in self.nodes:
            return {}

        try:
            # BFS to find depths
            depths = nx.single_source_shortest_path_length(
                self.graph, paper_id
            )
            return depths
        except Exception as e:
            logger.error(f"Error calculating citation depth: {e}")
            return {}

    def get_most_cited_papers(self, top_k: int = 10) -> List[PaperNode]:
        """
        Get most cited papers in the graph.

        Args:
            top_k: Number of papers to return

        Returns:
            List of most cited papers
        """
        nodes_with_citations = [
            (node, len(node.citations_in))
            for node in self.nodes.values()
        ]

        # Sort by citation count
        nodes_with_citations.sort(key=lambda x: x[1], reverse=True)

        return [node for node, _ in nodes_with_citations[:top_k]]

    def get_hub_papers(self, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Get hub papers (papers with many outgoing citations).

        Args:
            top_k: Number of papers to return

        Returns:
            List of (paper_id, hub_score) tuples
        """
        try:
            # Calculate hub scores using HITS algorithm
            hubs, _ = nx.hits(self.graph, max_iter=100)

            # Sort by hub score
            sorted_hubs = sorted(
                hubs.items(),
                key=lambda x: x[1],
                reverse=True
            )

            return sorted_hubs[:top_k]
        except Exception as e:
            logger.error(f"Error calculating hub scores: {e}")
            return []

    def get_authority_papers(self, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Get authority papers (papers with many incoming citations).

        Args:
            top_k: Number of papers to return

        Returns:
            List of (paper_id, authority_score) tuples
        """
        try:
            # Calculate authority scores using HITS algorithm
            _, authorities = nx.hits(self.graph, max_iter=100)

            # Sort by authority score
            sorted_authorities = sorted(
                authorities.items(),
                key=lambda x: x[1],
                reverse=True
            )

            return sorted_authorities[:top_k]
        except Exception as e:
            logger.error(f"Error calculating authority scores: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """
        Get graph statistics.

        Returns:
            Dict with graph stats
        """
        return {
            'total_papers': len(self.nodes),
            'total_citations': self.graph.number_of_edges(),
            'visited_papers': len(self.visited),
            'unvisited_papers': len(self.nodes) - len(self.visited),
            'avg_citations_per_paper': (
                self.graph.number_of_edges() / len(self.nodes)
                if len(self.nodes) > 0 else 0
            ),
            'connected_components': len(self.get_connected_components()),
            'is_dag': nx.is_directed_acyclic_graph(self.graph)
        }

    def clear(self):
        """Clear all data from the graph."""
        self.graph.clear()
        self.nodes.clear()
        self.visited.clear()
        logger.info("Citation graph cleared")

    def __len__(self) -> int:
        """Number of papers in graph."""
        return len(self.nodes)

    def __contains__(self, paper_id: str) -> bool:
        """Check if paper is in graph."""
        return paper_id in self.nodes
