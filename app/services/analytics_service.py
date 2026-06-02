"""
==============================================================
 Analytics Service — Business Intelligence Data Management
==============================================================
 Loads, parses, and serves analytics reports (markdown files)
 to the owner chatbot. Maintains structured in-memory storage
 for fast retrieval without vector search overhead.
==============================================================
"""

import logging
from datetime import datetime
from pathlib import Path
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Manages analytics report data for the business owner chatbot.
    
    Design Philosophy:
    - Analytics reports are loaded as STRUCTURED TEXT (not vectorized)
    - This preserves exact numbers, percentages, and financial data
    - Vector search would risk losing precision on numeric queries
    - Reports are split by markdown headers for section-level retrieval
    """
    
    def __init__(self, report_dir: str):
        self.report_dir = report_dir
        self._report_path = Path(report_dir)
        # Structure: {filename: {section_name: section_content}}
        self.reports: Dict[str, Dict[str, str]] = {}
        self.last_loaded: Optional[datetime] = None
        
        # Load all reports on initialization
        self.load_all_reports()
    
    def load_all_reports(self) -> int:
        """
        Loads all .md files from the owner analytics report directory.
        Returns the number of reports loaded.
        """
        if not self._report_path.exists():
            logger.warning("Owner analytics report directory not found: %s", self.report_dir)
            return 0
        
        loaded_count = 0
        
        for file_path in sorted(self._report_path.glob("*.md")):
            try:
                sections = self._parse_markdown_file(file_path)
                if sections:
                    self.reports[file_path.name] = sections
                    loaded_count += 1
                    logger.info(
                        "Loaded analytics report: %s (%d sections)",
                        file_path.name,
                        len(sections),
                    )
            except Exception as e:
                logger.error("Failed to load %s: %s", file_path.name, e)
        
        self.last_loaded = datetime.now()
        logger.info(
            "Analytics service loaded %d reports with %d total sections",
            loaded_count,
            self._total_sections(),
        )
        return loaded_count
    
    def _parse_markdown_file(self, file_path: Path | str) -> Dict[str, str]:
        """
        Parses a markdown file into sections based on ## headers.
        Returns a dict mapping section names to their content.
        """
        content = Path(file_path).read_text(encoding="utf-8")
        
        sections = {}
        
        # Split by level-2 headers (## Section Name)
        # Pattern: ## followed by section name, then content until next ## or end
        pattern = r'^##\s+(.+?)$'
        matches = list(re.finditer(pattern, content, re.MULTILINE))
        
        for i, match in enumerate(matches):
            section_name = match.group(1).strip()
            start_pos = match.end()
            
            # Find the end position (next ## or end of file)
            if i + 1 < len(matches):
                end_pos = matches[i + 1].start()
            else:
                end_pos = len(content)
            
            section_content = content[start_pos:end_pos].strip()
            sections[section_name] = section_content
        
        # Also capture the content before the first ## (usually title/metadata)
        if matches:
            preamble = content[:matches[0].start()].strip()
            if preamble:
                sections['_preamble'] = preamble
        else:
            # No sections found, treat entire content as one section
            sections['_full_content'] = content.strip()
        
        return sections
    
    def get_full_report(self) -> str:
        """
        Returns all analytics data as a single concatenated string.
        This is injected as LLM context for the owner chatbot.
        """
        if not self.reports:
            return "No analytics reports available."
        
        full_text_parts = []
        
        for filename, sections in self.reports.items():
            full_text_parts.append(f"=== REPORT: {filename} ===\n")
            
            # Add preamble first if it exists
            if '_preamble' in sections:
                full_text_parts.append(sections['_preamble'])
                full_text_parts.append("\n")
            
            # Add all sections
            for section_name, section_content in sections.items():
                if section_name.startswith('_'):
                    continue  # Skip internal keys
                full_text_parts.append(f"## {section_name}\n{section_content}\n")
        
        return "\n".join(full_text_parts)
    
    def get_section(self, section_keyword: str) -> str:
        """
        Retrieves content from sections matching the keyword (case-insensitive).
        
        Examples:
            get_section("financial") -> Returns "Financial Performance" section
            get_section("menu") -> Returns "Menu Analysis" section
        """
        section_keyword_lower = section_keyword.lower()
        matching_content = []
        
        for filename, sections in self.reports.items():
            for section_name, section_content in sections.items():
                if section_name.startswith('_'):
                    continue
                
                if section_keyword_lower in section_name.lower():
                    matching_content.append(f"## {section_name}\n{section_content}")
        
        if not matching_content:
            logger.debug("No sections found matching keyword: %s", section_keyword)
            return ""
        
        return "\n\n".join(matching_content)
    
    def get_available_sections(self) -> List[str]:
        """Returns a list of all available section names across all reports."""
        all_sections = set()
        
        for sections in self.reports.values():
            for section_name in sections.keys():
                if not section_name.startswith('_'):
                    all_sections.add(section_name)
        
        return sorted(list(all_sections))
    
    def reload(self) -> int:
        """
        Reloads all analytics reports from disk.
        Useful after local report files are added or changed.
        """
        logger.info("Reloading analytics reports...")
        self.reports.clear()
        return self.load_all_reports()
    
    def _total_sections(self) -> int:
        """Helper to count total sections across all reports."""
        total = 0
        for sections in self.reports.values():
            # Don't count internal keys
            total += sum(1 for key in sections.keys() if not key.startswith('_'))
        return total

