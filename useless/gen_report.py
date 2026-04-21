"""
MindCache Minor Project Report — Matching IIIT Bhopal ECE template exactly.
Font: Times New Roman, 12pt body, 20pt chapter heads, justified body text.
"""
from docx import Document
from docx.shared import Pt, Cm, Emu, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
import os

# Use the template as the base document to inherit its styles
doc = Document(r'e:\MindCache\2. ECE_Minor_project report format 6th sem.docx')

# Clear all existing content
for p in doc.paragraphs:
    p._element.getparent().remove(p._element)
for t in doc.tables:
    t._element.getparent().remove(t._element)

# ── Helpers ──
def blank(n=1):
    for _ in range(n):
        p = doc.add_paragraph()
        run = p.add_run('')
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)

def center(text, size=12, bold=False):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.space_before = Pt(0)
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(size)
    run.bold = bold
    return p

def justified(text, size=12, bold=False, space_after=6, first_indent=True):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.5
    if first_indent:
        p.paragraph_format.first_line_indent = Cm(1.27)
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(size)
    run.bold = bold
    return p

def justified_mixed(parts, space_after=6, first_indent=True):
    """parts = list of (text, bold) tuples"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.5
    if first_indent:
        p.paragraph_format.first_line_indent = Cm(1.27)
    for text, bold in parts:
        run = p.add_run(text)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)
        run.bold = bold
    return p

def left_text(text, size=12, bold=False):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(0)
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(size)
    run.bold = bold
    return p

def chapter_head(num, title):
    doc.add_page_break()
    blank(2)
    center(f'CHAPTER {num}', size=20, bold=True)
    center(title.upper(), size=20, bold=True)
    blank(1)

def section_head(text, size=14):
    blank()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.space_before = Pt(12)
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(size)
    run.bold = True
    return p

def subsection_head(text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.space_before = Pt(8)
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(12)
    run.bold = True
    run.italic = True

def bullet(text, bold_prefix=None):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.left_indent = Cm(1.27)
    p.paragraph_format.first_line_indent = Cm(-0.63)
    p.paragraph_format.line_spacing = 1.5
    if bold_prefix:
        r1 = p.add_run('\u2022 ' + bold_prefix)
        r1.font.name = 'Times New Roman'
        r1.font.size = Pt(12)
        r1.bold = True
        r2 = p.add_run(text)
        r2.font.name = 'Times New Roman'
        r2.font.size = Pt(12)
    else:
        r = p.add_run('\u2022 ' + text)
        r.font.name = 'Times New Roman'
        r.font.size = Pt(12)

def make_table(headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for j, h in enumerate(headers):
        cell = table.rows[0].cells[j]
        cell.text = ''
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(h)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(11)
        run.bold = True
    for i, row_data in enumerate(rows):
        for j, val in enumerate(row_data):
            cell = table.rows[i+1].cells[j]
            cell.text = ''
            p = cell.paragraphs[0]
            run = p.add_run(val)
            run.font.name = 'Times New Roman'
            run.font.size = Pt(11)
    return table

def table_caption(text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(10)
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(11)
    run.italic = True

# ═══════════════════════════════════════════════════════════════
#  TITLE PAGE
# ═══════════════════════════════════════════════════════════════
blank(4)
center('MINDCACHE: AN AI-POWERED SELF-ORGANIZING', size=16, bold=True)
center('KNOWLEDGE GRAPH SYSTEM FOR AUTONOMOUS', size=16, bold=True)
center('MEMORY MANAGEMENT', size=16, bold=True)
blank(2)
center('MINOR PROJECT REPORT', size=14, bold=True)
blank()
center('Submitted in partial fulfillment for the award of the degree of', size=12)
center('BACHELOR OF TECHNOLOGY', size=14, bold=True)
center('(Electronics and Communication Engineering)', size=12)
blank(2)
center('Submitted to', size=12)
center('INDIAN INSTITUTE OF INFORMATION TECHNOLOGY BHOPAL', size=14, bold=True)
center('(Madhya Pradesh)', size=12)
blank(2)
center('Submitted by', size=12)
blank()
center('Mohd Faisal Hussain (Scholar Number)', size=12, bold=True)
blank(2)
center('Under the supervision of', size=12)
center('Name of the Supervisor', size=12, bold=True)
center('Assistant Professor (ECE)', size=12)
blank(3)
center('April 2026', size=12, bold=True)
center('INDIAN INSTITUTE OF INFORMATION TECHNOLOGY BHOPAL', size=14, bold=True)

# ═══════════════════════════════════════════════════════════════
#  CERTIFICATE
# ═══════════════════════════════════════════════════════════════
doc.add_page_break()
blank(2)
center('CERTIFICATE', size=18, bold=True)
blank(2)

justified(
    'This is to certify that the Minor Project Report entitled '
    '\u201cMindCache: An AI-Powered Self-Organizing Knowledge Graph System for '
    'Autonomous Memory Management\u201d, submitted by Mohd Faisal Hussain in '
    'partial fulfillment of the requirements for the award of the degree of '
    'Bachelor of Technology in Electronics and Communication Engineering. '
    'This document is a comprehensive description of the proposed work and '
    'the work completed as part of the minor project evaluation.',
    space_after=12
)

blank(4)
left_text('Date:')
blank(6)
center('INDIAN INSTITUTE OF INFORMATION TECHNOLOGY BHOPAL', size=14.5, bold=True)

# ═══════════════════════════════════════════════════════════════
#  DECLARATION
# ═══════════════════════════════════════════════════════════════
doc.add_page_break()
blank(2)
center('DECLARATION', size=18, bold=True)
blank(2)

justified(
    'I hereby declare that the following Minor Project Report entitled '
    '\u201cMindCache: An AI-Powered Self-Organizing Knowledge Graph System for '
    'Autonomous Memory Management\u201d presented in the partial fulfillment of '
    'the requirements for the award of the degree of Bachelor of Technology '
    'in Electronics and Communication Engineering. It is an authentic '
    'documentation of my original work carried out under the guidance of '
    'Name of Supervisor. The work has been carried out entirely at the '
    'Indian Institute of Information Technology, Bhopal. The project work '
    'presented has not been submitted in part or whole to award of any '
    'degree or professional diploma in any other institute or organization.',
    space_after=12
)

justified(
    'I, with this, declare that the facts mentioned above are true to the '
    'best of my knowledge. In case of any unlikely discrepancy that may '
    'occur, I will be the one to take responsibility.',
    space_after=12
)

blank(3)
left_text('Mohd Faisal Hussain (Scholar Number)\t\t\t\t          Signature')

# ═══════════════════════════════════════════════════════════════
#  AREA OF WORK
# ═══════════════════════════════════════════════════════════════
doc.add_page_break()
blank(2)
center('AREA OF WORK', size=20, bold=True)
blank(1)

justified(
    'This project focuses on Artificial Intelligence, Natural Language '
    'Processing, and Graph-Based Knowledge Representation. The core '
    'challenge addressed is the autonomous organization and retrieval of '
    'unstructured digital context \u2014 a problem that sits at the intersection '
    'of information retrieval, LLM orchestration, and dynamic graph algorithms.'
)

justified(
    'MindCache is a full-stack AI system that passively captures a user\u2019s '
    'digital context (browsing activity, code sessions, research notes, '
    'error logs) through a browser extension, processes it through a '
    'multi-stage NLP pipeline, and autonomously organizes the extracted '
    'knowledge into a hierarchical, self-restructuring topic tree. Unlike '
    'traditional RAG (Retrieval-Augmented Generation) systems that rely on '
    'flat vector databases, MindCache maintains a living knowledge graph '
    'that continuously grooms, merges, and reorganizes itself to minimize '
    'redundancy and maximize retrieval precision.'
)

justified(
    'The system employs multiple specialized AI agents \u2014 an Input Denoiser '
    'for data preprocessing, a Memory Extractor for semantic parsing, a '
    'Decision State Analyzer for temporal reasoning, and a Tree Reorganizer '
    'for graph maintenance \u2014 each operating as independent components '
    'within a unified FastAPI-based backend architecture. The retrieval '
    'pipeline implements a novel 5-phase adaptive search strategy that '
    'combines vector embeddings with a custom BM25 keyword scorer, achieving '
    '92% accuracy (47/51) on complex long-term memory evaluation benchmarks.'
)

# ═══════════════════════════════════════════════════════════════
#  TABLE OF CONTENTS
# ═══════════════════════════════════════════════════════════════
doc.add_page_break()
blank(2)
center('TABLE OF CONTENTS', size=20, bold=True)
blank(1)

toc = [
    (False, 'Certificate'),
    (False, 'Declaration'),
    (False, 'Area of Work'),
    (False, 'Table of Contents'),
    (False, 'List of Figures'),
    (False, 'List of Tables'),
    (True,  'Chapter 1: INTRODUCTION'),
    (False, '    1.1 Background and Motivation'),
    (False, '    1.2 Problem Statement'),
    (False, '    1.3 Objectives'),
    (False, '    1.4 Scope of the Project'),
    (True,  'Chapter 2: LITERATURE REVIEW'),
    (False, '    2.1 Traditional Memory and Retrieval Systems'),
    (False, '    2.2 LLM-Based Knowledge Extraction'),
    (False, '    2.3 Graph-Based Knowledge Representation'),
    (False, '    2.4 Hybrid Retrieval Strategies'),
    (False, '    2.5 Gaps in Existing Work'),
    (True,  'Chapter 3: PROPOSED METHODOLOGY AND WORK DESCRIPTION'),
    (False, '    3.1 System Overview'),
    (False, '    3.2 Data Ingestion Layer'),
    (False, '    3.3 Input Denoising Pipeline'),
    (False, '    3.4 LLM-Based Memory Extraction'),
    (False, '    3.5 Hierarchical Tree Organization'),
    (False, '    3.6 Decision State Analysis'),
    (False, '    3.7 Multi-Phase Retrieval Pipeline'),
    (False, '    3.8 Fine-Tuning Data Curation'),
    (True,  'Chapter 4: PROPOSED ALGORITHMS'),
    (False, '    4.1 Tri-State Input Classification Algorithm'),
    (False, '    4.2 Code Skeletonizer State Machine'),
    (False, '    4.3 Fuzzy-Hash Log Deduplication Algorithm'),
    (False, '    4.4 Tree Reorganization Multi-Phase Algorithm'),
    (False, '    4.5 Hybrid BM25 + Vector Scoring Algorithm'),
    (False, '    4.6 Adaptive Root Descent Algorithm'),
    (True,  'Chapter 5: PROPOSED FLOWCHART / BLOCK DIAGRAM'),
    (False, '    5.1 System Architecture Block Diagram'),
    (False, '    5.2 Data Ingestion Flow'),
    (False, '    5.3 Memory Extraction Pipeline Flow'),
    (False, '    5.4 Tree Reorganization Flow'),
    (False, '    5.5 Retrieval Pipeline Flow'),
    (True,  'Chapter 6: TOOLS AND TECHNOLOGY USED'),
    (True,  'Chapter 7: CONCLUSION AND FUTURE SCOPE'),
    (False, '    7.1 Conclusion'),
    (False, '    7.2 Results and Evaluation'),
    (False, '    7.3 Future Scope'),
    (True,  'REFERENCES'),
]

for is_bold, text in toc:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.line_spacing = 1.5
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(12)
    run.bold = is_bold

# ═══════════════════════════════════════════════════════════════
#  LIST OF FIGURES
# ═══════════════════════════════════════════════════════════════
doc.add_page_break()
blank(2)
center('LIST OF FIGURES', size=20, bold=True)
blank(1)

figs = [
    'Figure 1.1: High-Level System Architecture of MindCache',
    'Figure 3.1: Complete System Pipeline Block Diagram',
    'Figure 3.2: Browser Extension Data Flow',
    'Figure 3.3: Tri-State Input Denoiser Classification Flow',
    'Figure 3.4: Code Skeletonizer State Machine Transitions',
    'Figure 3.5: Memory Extraction Pipeline',
    'Figure 3.6: Hierarchical Topic Tree Structure (Example)',
    'Figure 3.7: Tree Reorganization Multi-Phase Pipeline',
    'Figure 3.8: Decision State Analyzer Workflow',
    'Figure 5.1: System Architecture Block Diagram',
    'Figure 5.2: Data Ingestion Flow Diagram',
    'Figure 5.3: Memory Extraction Pipeline Flow',
    'Figure 5.4: Tree Reorganization Flow Diagram',
    'Figure 5.5: 5-Phase Retrieval Pipeline Flow',
]
for f in figs:
    left_text(f)

# ═══════════════════════════════════════════════════════════════
#  LIST OF TABLES
# ═══════════════════════════════════════════════════════════════
doc.add_page_break()
blank(2)
center('LIST OF TABLES', size=20, bold=True)
blank(1)

tabs = [
    'Table 3.1: Memory Types and Their Attributes',
    'Table 3.2: Tree Reorganization Phase Summary',
    'Table 3.3: Retrieval Pipeline Phase Descriptions',
    'Table 4.1: BM25 Scoring Parameters',
    'Table 6.1: Tools and Technologies Used',
    'Table 7.1: LongMemEval Benchmark Results',
    'Table 7.2: Comparison with Existing Systems',
]
for t in tabs:
    left_text(t)

# ═══════════════════════════════════════════════════════════════
#  CHAPTER 1: INTRODUCTION
# ═══════════════════════════════════════════════════════════════
chapter_head(1, 'Introduction')

section_head('1.1 Background and Motivation')

justified(
    'The rapid proliferation of digital information has created an '
    'unprecedented challenge for knowledge workers, researchers, and '
    'developers: the inability to efficiently recall and retrieve '
    'contextual information from their own digital activities. A software '
    'developer may spend hours debugging an issue they have already solved '
    'months ago, or a researcher may fail to connect insights from '
    'disparate reading sessions. The human brain is optimized for pattern '
    'recognition and creative synthesis, not for the faithful storage and '
    'retrieval of large volumes of factual, episodic, and procedural knowledge.'
)

justified(
    'Recent advances in Large Language Models (LLMs) have made it possible '
    'to extract structured semantic information from unstructured natural '
    'language text with remarkable accuracy. Simultaneously, vector embedding '
    'techniques have enabled semantic similarity search at scale. However, '
    'most existing AI-powered knowledge management systems \u2014 commonly '
    'referred to as \u201csecond brain\u201d applications \u2014 suffer from a '
    'fundamental architectural limitation: they treat knowledge storage as '
    'a flat, append-only vector database problem.'
)

justified(
    'This flat-storage paradigm fails to capture the hierarchical, temporal, '
    'and relational nature of real-world knowledge. Decisions evolve over '
    'time \u2014 what a user decided last month may have been superseded by '
    'new information. Topics have natural hierarchical relationships \u2014 '
    '\u201cMachine Learning\u201d subsumes \u201cNeural Networks\u201d, which '
    'subsumes \u201cConvolutional Neural Networks\u201d. These structural '
    'relationships are lost in a flat vector store.'
)

justified(
    'MindCache was conceived to address this gap. Rather than treating '
    'knowledge as an unstructured bag of text chunks, MindCache autonomously '
    'organizes extracted memories into a self-restructuring hierarchical '
    'topic tree \u2014 a living knowledge graph that continuously grooms, '
    'merges, splits, and reorganizes itself to maintain semantic coherence '
    'as new information flows in.'
)

section_head('1.2 Problem Statement')

justified(
    'Existing AI-powered personal knowledge management systems rely on flat '
    'vector databases for storage and brute-force semantic similarity search '
    'for retrieval. This approach suffers from three critical limitations: '
    '(1) loss of hierarchical structure \u2014 the natural taxonomy of '
    'knowledge is destroyed when converted to flat embeddings; (2) absence '
    'of temporal reasoning \u2014 the system cannot determine whether a '
    'stored decision is still valid or has been superseded; and (3) poor '
    'scalability of retrieval precision \u2014 as the knowledge base grows, '
    'naive vector search returns increasingly noisy results because it lacks '
    'structural context to narrow the search space.'
)

justified(
    'This project proposes a solution that addresses all three limitations '
    'through a self-organizing hierarchical knowledge graph with autonomous '
    'graph maintenance and a multi-phase adaptive retrieval pipeline.'
)

section_head('1.3 Objectives')

justified('The primary objectives of this project are:', first_indent=False)

bullet('To design and implement an intelligent preprocessing pipeline (Input Denoiser) that classifies and compresses raw input data (code, logs, natural language) to minimize token usage before LLM processing.')
bullet('To develop an LLM-based memory extraction engine capable of parsing unstructured text into typed, schema-constrained memory chains (episodic, knowledge, decision, preference).')
bullet('To engineer a self-organizing hierarchical topic tree with autonomous graph maintenance capabilities including root orthogonality enforcement, cycle resolution, sibling deduplication, fan-out limiting, and overloaded leaf auto-splitting.')
bullet('To implement a temporal Decision State Analyzer that autonomously tracks the validity of decision-type memories by cross-referencing them against surrounding episodic and knowledge context.')
bullet('To build a multi-phase adaptive retrieval pipeline combining vector embeddings with a custom BM25 keyword scorer for precise, context-aware information retrieval.')
bullet('To create a fine-tuning data curation pipeline that transforms real-world system usage into supervised training datasets for model specialization.')
bullet('To achieve at least 90% retrieval accuracy on complex long-term memory evaluation benchmarks.')

section_head('1.4 Scope of the Project')

justified(
    'The scope of MindCache encompasses the complete end-to-end pipeline '
    'from data ingestion to knowledge retrieval. The system includes: '
    '(a) a Chrome browser extension for passive data capture from browsing '
    'sessions; (b) a FastAPI backend server for request routing and queue '
    'management; (c) a multi-stage NLP preprocessing pipeline; (d) an '
    'LLM-powered memory extraction engine; (e) a 1500+ line graph '
    'restructuring engine for autonomous tree maintenance; (f) a Decision '
    'State Analyzer for temporal reasoning; (g) a 5-phase adaptive '
    'retrieval pipeline; (h) a fine-tuning data curation pipeline; and '
    '(i) a web-based visualization interface for graph exploration and '
    'query testing.'
)

# ═══════════════════════════════════════════════════════════════
#  CHAPTER 2: LITERATURE REVIEW
# ═══════════════════════════════════════════════════════════════
chapter_head(2, 'Literature Review')

section_head('2.1 Traditional Memory and Retrieval Systems')

justified(
    'The concept of augmenting human memory with digital systems dates back '
    'to Vannevar Bush\u2019s seminal 1945 essay \u201cAs We May Think\u201d, '
    'which proposed the Memex \u2014 a hypothetical device for storing and '
    'retrieving an individual\u2019s books, records, and communications. '
    'Modern implementations of this vision include personal knowledge '
    'management (PKM) tools such as Obsidian, Notion, and Roam Research, '
    'which organize information using manual tagging, linking, and '
    'hierarchical folder structures. While effective for users who invest '
    'time in explicit organization, these systems require significant '
    'manual effort and do not autonomously extract or structure knowledge '
    'from raw digital activity.'
)

section_head('2.2 LLM-Based Knowledge Extraction')

justified(
    'The emergence of Large Language Models, particularly the GPT family '
    '(OpenAI, 2020-2024), LLaMA (Meta, 2023), and Qwen (Alibaba, 2024), '
    'has revolutionized the ability to extract structured information from '
    'unstructured text. Techniques such as few-shot prompting, '
    'chain-of-thought reasoning, and constrained JSON output generation '
    'have enabled LLMs to function as reliable information extraction '
    'engines. Notable work in this area includes structured extraction '
    'frameworks like Instructor (Liu, 2023) and DSPy (Khattab et al., '
    '2023), which provide programmatic interfaces for LLM-based data '
    'extraction with schema validation. MindCache builds upon these '
    'foundations by implementing a multi-pass extraction pipeline that '
    'uses Pydantic schema validation to ensure type-safe, structured '
    'output from local LLMs running via Ollama.'
)

section_head('2.3 Graph-Based Knowledge Representation')

justified(
    'Knowledge graphs have been extensively studied as a means of '
    'representing structured relationships between entities. Notable '
    'systems include Google\u2019s Knowledge Graph (Singhal, 2012), '
    'Wikidata (Vrande\u010di\u0107 & Kr\u00f6tzsch, 2014), and '
    'domain-specific ontologies using RDF and OWL frameworks. In the '
    'personal knowledge management space, systems like MemPalace (2025) '
    'have adopted spatial metaphors (the ancient Greek method of loci) '
    'to organize memories into wings, rooms, and drawers. While MemPalace '
    'achieves impressive retrieval benchmarks (96.6% on LongMemEval), its '
    'graph structure is static \u2014 pre-defined spatial partitions do not '
    'adapt as the knowledge base evolves. MindCache addresses this '
    'limitation by implementing a dynamic, self-restructuring hierarchical '
    'topic tree that autonomously maintains its own organization.'
)

section_head('2.4 Hybrid Retrieval Strategies')

justified(
    'Modern information retrieval research has demonstrated that combining '
    'dense vector retrieval (semantic similarity via embeddings) with '
    'sparse retrieval (keyword matching via BM25) yields superior results '
    'compared to either approach alone. The BM25 algorithm (Robertson & '
    'Zaragoza, 2009) remains the gold standard for keyword-based retrieval, '
    'while dense retrieval using transformer-based embeddings (Karpukhin et '
    'al., 2020) captures semantic meaning beyond lexical overlap. Hybrid '
    'approaches such as those used in ColBERT (Khattab & Zaharia, 2020) '
    'and RRF (Reciprocal Rank Fusion) have shown significant improvements. '
    'MindCache implements its own hybrid scoring mechanism with a '
    'configurable 60/40 weighting between vector similarity and a '
    'custom-built BM25 scorer.'
)

section_head('2.5 Gaps in Existing Work')

justified(
    'Despite significant progress in each of these individual areas, no '
    'existing system combines all four capabilities: (1) intelligent '
    'preprocessing that reduces token waste before LLM processing; '
    '(2) autonomous graph restructuring that maintains semantic coherence '
    'without human intervention; (3) temporal decision state tracking '
    'that distinguishes between active and superseded knowledge; and '
    '(4) multi-phase adaptive retrieval that leverages hierarchical graph '
    'structure to narrow the search space. MindCache is designed to fill '
    'this gap by integrating all four capabilities into a unified, '
    'end-to-end system.'
)

# ═══════════════════════════════════════════════════════════════
#  CHAPTER 3: PROPOSED METHODOLOGY AND WORK DESCRIPTION
# ═══════════════════════════════════════════════════════════════
chapter_head(3, 'Proposed Methodology and Work Description')

section_head('3.1 System Overview')

justified(
    'MindCache operates as a multi-layered AI pipeline with six core '
    'subsystems, each responsible for a distinct phase of the knowledge '
    'management lifecycle. The system accepts raw, unstructured digital '
    'context as input and produces a queryable, hierarchically organized '
    'knowledge graph as output. The six subsystems are: (1) Data Ingestion '
    'Layer, (2) Input Denoising Pipeline, (3) LLM-Based Memory Extraction, '
    '(4) Hierarchical Tree Organization and Maintenance, (5) Decision '
    'State Analysis, and (6) Multi-Phase Adaptive Retrieval.'
)

section_head('3.2 Data Ingestion Layer')

justified(
    'The data ingestion layer consists of a Chrome browser extension that '
    'passively captures the user\u2019s browsing context. The extension '
    'operates in two modes: (a) passive scraping mode, which monitors '
    'active browser tabs and extracts visible text content at configurable '
    'intervals; and (b) manual selection mode, which allows the user to '
    'highlight specific text passages for ingestion. Captured data is '
    'transmitted to the FastAPI backend server via REST API endpoints. '
    'The backend server manages request queuing, authentication, and '
    'routes incoming data to the appropriate processing pipeline.'
)

section_head('3.3 Input Denoising Pipeline')

justified(
    'Raw ingested data is typically noisy \u2014 it contains mixed code '
    'snippets, error log traces, HTML artifacts, and natural language '
    'text interspersed with formatting noise. Feeding this raw data '
    'directly to an LLM wastes valuable context window tokens and '
    'degrades extraction quality.'
)

subsection_head('3.3.1 Tri-State Classification Router')

justified(
    'The Input Denoiser implements a tri-state classification system that '
    'processes each line of input and classifies it into one of three '
    'categories: CODE, LOG, or TEXT. Classification is performed using two '
    'sets of compiled regular expression patterns \u2014 code_signals '
    '(detecting import statements, function definitions, class declarations, '
    'decorators, and bracket patterns) and log_signals (detecting timestamps, '
    'log levels, stack traces, and error codes). Lines matching code patterns '
    'are routed to the Code Compressor; lines matching log patterns are '
    'routed to the Log Compressor; remaining lines are treated as natural '
    'language text and passed through with minimal processing.'
)

subsection_head('3.3.2 Code Skeletonizer (State Machine)')

justified(
    'Code blocks are processed by a state-machine-based \u201cskeletonizer\u201d '
    'that strips function and method bodies while preserving architecturally '
    'significant elements: import statements, class declarations, function '
    'signatures (including decorators and type hints), module-level constants, '
    'and structural comments (TODO, FIXME, HACK annotations). The state '
    'machine tracks indentation depth to determine function body boundaries, '
    'emitting a \u201c...\u201d placeholder for stripped bodies. This preserves '
    'the architectural intent of the code without wasting tokens on '
    'implementation details.'
)

subsection_head('3.3.3 Fuzzy-Hash Log Deduplicator')

justified(
    'Error log segments are processed by a fuzzy-hash deduplication engine. '
    'The engine first stitches multi-line log entries (such as Python '
    'tracebacks) into complete units. It then creates a \u201cfuzzy hash\u201d '
    'of each log entry by masking dynamic values \u2014 UUIDs, IP addresses, '
    'hexadecimal memory addresses, timestamps, and numeric identifiers \u2014 '
    'with placeholder tokens. This normalized representation allows the '
    'deduplicator to identify structurally identical log entries that differ '
    'only in their dynamic values. Duplicate structures are collapsed into '
    'a single representative instance with a repetition count annotation. '
    'This compression technique typically achieves 40-60% token reduction '
    'before LLM processing.'
)

section_head('3.4 LLM-Based Memory Extraction')

justified(
    'Denoised input is processed by the Memory Extractor module, which '
    'uses LLMs (running locally via Ollama, primarily Qwen 1.7B/4B '
    'models) to parse unstructured text into typed memory chains. The '
    'extraction engine produces four types of memories:'
)

blank()
make_table(
    ['Memory Type', 'Description', 'Example'],
    [
        ['Episodic', 'Events, sessions, debugging episodes with temporal context', 'Debugged auth token refresh for 2 hours on March 10'],
        ['Knowledge', 'Factual information, technical concepts, learned insights', 'FastAPI uses Starlette for async routing'],
        ['Decision', 'Choices made with rationale, subject to temporal validity', 'Chose SQLite over PostgreSQL for simplicity'],
        ['Preference', 'User habits, opinions, workflow preferences', 'Always use type hints in Python functions'],
    ]
)
table_caption('Table 3.1: Memory Types and Their Attributes')

justified(
    'Each extracted memory is tagged with a topic chain (e.g., '
    '[\u201cBackend\u201d, \u201cDatabase\u201d, \u201cMigrations\u201d]), '
    'a timestamp, and a confidence score. The extraction process uses '
    'Pydantic schema validation (via the SafeAI module) to ensure that '
    'LLM outputs conform to the expected JSON structure. Failed validations '
    'trigger automatic retry with error feedback injection into the prompt.'
)

section_head('3.5 Hierarchical Tree Organization')

justified(
    'The Tree Reorganizer (reorganize_tree.py, 1530 lines) is the '
    'engineering core of MindCache. It maintains a multi-rooted '
    'hierarchical topic tree where each root represents a top-level '
    'knowledge domain (e.g., \u201cComputer Science\u201d, \u201cHealth\u201d, '
    '\u201cLaw\u201d), and leaf nodes store actual memory references. '
    'The reorganizer operates in multiple phases:'
)

blank()
make_table(
    ['Phase', 'Name', 'Description'],
    [
        ['Phase 0', 'Root Orthogonality', 'Ensures top-level root domains are mutually exclusive'],
        ['Phase 1', 'Global Bootstrap', 'Full tree restructuring for small databases'],
        ['Phase 2', 'Targeted Vector Grooming', 'Hybrid BM25 + vector similarity matching of ungroomed chains'],
        ['Phase 3', 'Fan-Out Limiting', 'Clusters children when a parent exceeds 10 child nodes'],
        ['Phase 4', 'Leaf Splitting', 'Auto-splits leaf nodes with 25+ memories into sub-categories'],
        ['Phase 5', 'Post-Processing', 'Sibling dedup, orphan cleanup, cycle detection and resolution'],
    ]
)
table_caption('Table 3.2: Tree Reorganization Phase Summary')

justified(
    'A critical engineering challenge in the reorganizer is token-aware '
    'bin-packing: the system groups tree branches into optimally-sized '
    'batches (using a min_groups algorithm) so that each LLM restructuring '
    'call utilizes maximum context without exceeding the model\u2019s token '
    'limit. The reorganizer also maintains an embedding cache, updating '
    'or invalidating cached topic embeddings whenever the tree structure changes.'
)

section_head('3.6 Decision State Analysis')

justified(
    'The Decision State Analyzer (decision_analyzer.py) is a specialized '
    'LLM agent that addresses the temporal validity problem in knowledge '
    'management. When a user makes a decision (e.g., \u201cUse PostgreSQL '
    'for the backend database\u201d), that decision may later be superseded '
    'by new information. The Decision Analyzer periodically scans '
    'decision-type memories and cross-references each decision against '
    'surrounding episodic and knowledge memories within a configurable '
    'time window.'
)

justified(
    'For each decision, the analyzer classifies its current state into one '
    'of five categories: ACTIVE (still valid), SUPERSEDED (replaced by a '
    'newer decision), REJECTED (explicitly abandoned), CONDITIONAL (valid '
    'only under specific circumstances), or INACTIVE (no longer relevant). '
    'This temporal reasoning capability ensures that retrieval results '
    'reflect the current state of the user\u2019s knowledge.'
)

section_head('3.7 Multi-Phase Retrieval Pipeline')

justified(
    'Querying the knowledge graph is performed through a 5-phase adaptive '
    'retrieval pipeline (active_path.py), designed to leverage the '
    'hierarchical structure of the topic tree for precise, context-aware '
    'retrieval:'
)

blank()
make_table(
    ['Phase', 'Component', 'Function'],
    [
        ['Phase 1', 'Context Bridge', 'Constructs composite query vector from current prompt and conversation history'],
        ['Phase 2', 'Root Search', 'Identifies 1-6 most relevant root domains via vector similarity'],
        ['Phase 3', 'Root Descent', 'Recursively walks tree using hybrid 60/40 vector + BM25 scoring'],
        ['Phase 4', 'Agentic Refiner', 'LLM classifies intent and selects 1-3 topics with depth decision'],
        ['Phase 5', 'DB Fetch', 'Retrieves memories from SQLite with decision-state filters applied'],
    ]
)
table_caption('Table 3.3: Retrieval Pipeline Phase Descriptions')

section_head('3.8 Fine-Tuning Data Curation')

justified(
    'To address the limitations of base LLMs in producing consistently '
    'schema-compliant structured output, MindCache includes a fine-tuning '
    'data curation pipeline (prepare_finetuning_data.py). This pipeline '
    'transforms real-world MindCache usage data \u2014 actual ingestion '
    'inputs paired with their corresponding validated extraction outputs '
    '\u2014 into supervised fine-tuning examples. The curated dataset (16MB+, '
    'stored as finetuning_dataset.json) is used to specialize small, '
    'resource-efficient local models (Qwen 1.7B) for the specific task '
    'of schema-constrained memory extraction.'
)

# ═══════════════════════════════════════════════════════════════
#  CHAPTER 4: PROPOSED ALGORITHMS
# ═══════════════════════════════════════════════════════════════
chapter_head(4, 'Proposed Algorithms')

section_head('4.1 Tri-State Input Classification Algorithm')

justified('The classification algorithm operates on each input line:', first_indent=False)
blank()
justified('Step 1: For each line, compute code_score by matching against code_signals regex patterns (import statements, function definitions, class declarations, decorators).', first_indent=False, space_after=3)
justified('Step 2: Compute log_score by matching against log_signals regex patterns (timestamps, log levels, stack traces, error codes).', first_indent=False, space_after=3)
justified('Step 3: If code_score > log_score and code_score > 0, classify as CODE.', first_indent=False, space_after=3)
justified('Step 4: Else if log_score > 0, classify as LOG.', first_indent=False, space_after=3)
justified('Step 5: Otherwise, classify as TEXT.', first_indent=False, space_after=3)
justified('Step 6: Buffer consecutive lines of same type into segments.', first_indent=False, space_after=3)
justified('Step 7: Route CODE segments to Code Skeletonizer, LOG segments to Fuzzy-Hash Deduplicator, TEXT segments pass through.', first_indent=False, space_after=6)

section_head('4.2 Code Skeletonizer State Machine')

justified(
    'The code skeletonizer operates as a finite state machine with three '
    'states: SCAN, INSIDE_FUNCTION, and SKIP_BODY.'
)

justified('Transitions:', first_indent=False, bold=True, space_after=3)
bullet('SCAN \u2192 INSIDE_FUNCTION: When a function/method signature (def, class) is detected.')
bullet('INSIDE_FUNCTION \u2192 SKIP_BODY: After emitting the signature, enter body-skip mode.')
bullet('SKIP_BODY \u2192 SCAN: When indentation returns to or below the function\u2019s base level.')

justified('Preserved Elements (always emitted):', first_indent=False, bold=True, space_after=3)
bullet('Import statements (import, from...import)')
bullet('Class and function signatures (with decorators and type hints)')
bullet('Module-level constants and assignments')
bullet('Structural comments (# TODO, # FIXME, # HACK, # NOTE)')

justified('Stripped Elements (replaced with \u201c...\u201d):', first_indent=False, bold=True, space_after=3)
bullet('Function/method bodies')
bullet('Loop bodies at non-module level')
bullet('Conditional branches at non-module level')

section_head('4.3 Fuzzy-Hash Log Deduplication Algorithm')

justified('Step 1 \u2014 STITCH: Join multi-line log entries (e.g., Python tracebacks) into single records by detecting continuation lines.', first_indent=False, space_after=3)
justified('Step 2 \u2014 NORMALIZE: Replace UUIDs with <UUID>, IP addresses with <IP>, hex addresses with <HEX>, numeric sequences with <NUM>, timestamps with <TIME>.', first_indent=False, space_after=3)
justified('Step 3 \u2014 HASH: Compute fuzzy hash of the normalized string.', first_indent=False, space_after=3)
justified('Step 4 \u2014 DEDUP: Use hash table to detect duplicates. If hash seen, increment counter and skip. If new, add to output.', first_indent=False, space_after=3)
justified('Step 5 \u2014 ANNOTATE: Append (\u00d7N) count to deduplicated entries where N > 1.', first_indent=False, space_after=6)

section_head('4.4 Tree Reorganization Multi-Phase Algorithm')

justified(
    'The tree reorganization algorithm (1530 lines) is the most complex '
    'component of MindCache. The algorithm accepts the current topic tree '
    'T, a set of new ungroomed chains C, and the embedding cache E as '
    'input, and produces a restructured tree T\u2019 as output.'
)

justified('Phase 0 \u2014 Root Orthogonality:', first_indent=False, bold=True, space_after=3)
justified('For each pair of roots, compute embedding similarity. If similarity exceeds threshold, merge the less specific root into the more specific one using LLM-guided decision. Dissolve generic roots (e.g., \u201cGeneral\u201d, \u201cMiscellaneous\u201d).', space_after=6)

justified('Phase 2 \u2014 Targeted Vector Grooming (large databases):', first_indent=False, bold=True, space_after=3)
justified('For each ungroomed chain, compute its embedding and find the top-k most similar existing branches using cosine similarity. Compute BM25 score against candidate branch names. Rank by hybrid score = 0.6 * sim + 0.4 * bm25. Group chains into LLM batches using token-aware bin-packing (min_groups greedy algorithm). For each batch, invoke LLM to produce restructured subtree. Apply structural mutations and resolve any cycles.', space_after=6)

justified('Post-Processing:', first_indent=False, bold=True, space_after=3)
bullet('group_overgrown_children(): ', 'If any node has >10 children, cluster them into sub-groups using LLM.')
bullet('split_overloaded_leaves(): ', 'If any leaf has >25 memories, subdivide into meaningful sub-categories.')
bullet('Sibling deduplication: merge nodes with >0.9 embedding similarity.')
bullet('Orphan cleanup: re-attach or remove nodes with no valid parent.')

section_head('4.5 Hybrid BM25 + Vector Scoring Algorithm')

justified(
    'The BM25 scorer is implemented from scratch following the Okapi BM25 '
    'formulation. The term frequency component uses saturation parameter '
    'k1 = 1.5 and length normalization parameter b = 0.75. The inverse '
    'document frequency is computed as IDF(qi) = log((N - n(qi) + 0.5) / '
    '(n(qi) + 0.5) + 1), where N is the total number of documents and '
    'n(qi) is the number of documents containing term qi.'
)

justified(
    'The hybrid score combines BM25 with vector cosine similarity: '
    'hybrid_score = \u03b1 \u00b7 cosine_sim(query_vec, topic_vec) + '
    '(1-\u03b1) \u00b7 normalize(BM25(q, d)), where \u03b1 = 0.6 '
    '(configurable via RetrievalConfig).'
)

section_head('4.6 Adaptive Root Descent Algorithm')

justified('Step 1: Receive query_vector and current_node (starting from selected root).', first_indent=False, space_after=3)
justified('Step 2: Fetch all children of current_node.', first_indent=False, space_after=3)
justified('Step 3: For each child, compute hybrid_score (vector + BM25).', first_indent=False, space_after=3)
justified('Step 4: Filter children with hybrid_score > descent_threshold (0.20).', first_indent=False, space_after=3)
justified('Step 5: If filtered children \u2264 3, use vector-based selection (top-k by score). If > 3 and scores are ambiguous (std_dev < 0.05), escalate to LLM-based Adaptive Node Selector.', first_indent=False, space_after=3)
justified('Step 6: For each selected child \u2014 if leaf, add to candidate list; if branch, recurse.', first_indent=False, space_after=3)
justified('Step 7: Return ranked list of up to top_k (20) candidate topics.', first_indent=False, space_after=6)

# ═══════════════════════════════════════════════════════════════
#  CHAPTER 5: PROPOSED FLOWCHART / BLOCK DIAGRAM
# ═══════════════════════════════════════════════════════════════
chapter_head(5, 'Proposed Flowchart / Block Diagram')

justified(
    'This chapter presents the key system architecture diagrams and data '
    'flow visualizations for MindCache. Due to the complexity of the system, '
    'individual block diagrams are provided for each major subsystem.'
)

section_head('5.1 System Architecture Block Diagram')

justified(
    'The overall system architecture follows a layered pipeline design. '
    'At the ingestion layer, a Chrome browser extension captures raw '
    'digital context and transmits it to a FastAPI backend server. The '
    'server routes data through the Input Denoiser, which classifies and '
    'compresses input before passing it to the Memory Extractor. Extracted '
    'memories are stored in an SQLite database and the topic tree is '
    'updated by the Reorganizer. On the retrieval side, user queries '
    'flow through a 5-phase pipeline: Context Bridge, Root Search, Root '
    'Descent, Agentic Refiner, and Database Fetch.'
)

justified('[Figure 5.1: System Architecture Block Diagram \u2014 to be inserted]', bold=True, first_indent=False)

section_head('5.2 Data Ingestion Flow')

justified(
    'The data ingestion flow begins when a user browses the web or works '
    'on code. The browser extension passively captures visible text content '
    'and transmits it to the /ingest API endpoint. The FastAPI server queues '
    'the request and processes it asynchronously through the Denoiser, '
    'Extractor, and Reorganizer pipeline.'
)

justified('[Figure 5.2: Data Ingestion Flow Diagram \u2014 to be inserted]', bold=True, first_indent=False)

section_head('5.3 Memory Extraction Pipeline Flow')

justified(
    'Raw input enters the tri-state classifier, which routes code to the '
    'Skeletonizer, logs to the Fuzzy-Hash Deduplicator, and text through '
    'with minimal processing. The compressed output is passed to the LLM '
    'Memory Extractor, which produces structured memory chains validated '
    'by Pydantic schemas. Valid extractions are stored in the database '
    'and trigger a tree reorganization pass.'
)

justified('[Figure 5.3: Memory Extraction Pipeline Flow \u2014 to be inserted]', bold=True, first_indent=False)

section_head('5.4 Tree Reorganization Flow')

justified(
    'New memory chains enter the reorganizer, which first checks root '
    'orthogonality (Phase 0), then performs either a global bootstrap '
    '(Phase 1, for small databases) or targeted vector grooming '
    '(Phase 2, for large databases). Post-processing handles fan-out '
    'limiting, leaf splitting, sibling deduplication, and orphan cleanup. '
    'Finally, the embedding cache is updated for all affected nodes.'
)

justified('[Figure 5.4: Tree Reorganization Flow Diagram \u2014 to be inserted]', bold=True, first_indent=False)

section_head('5.5 Retrieval Pipeline Flow')

justified(
    'A user query enters Phase 1 (Context Bridge), which builds a composite '
    'query vector. Phase 2 (Root Search) identifies relevant root domains. '
    'Phase 3 (Root Descent) recursively walks the tree using hybrid scoring. '
    'Phase 4 (Agentic Refiner) uses an LLM to classify query intent and '
    'select the best 1-3 topics with depth decisions. Phase 5 (DB Fetch) '
    'retrieves actual memories with decision-state filters applied.'
)

justified('[Figure 5.5: 5-Phase Retrieval Pipeline Flow \u2014 to be inserted]', bold=True, first_indent=False)

# ═══════════════════════════════════════════════════════════════
#  CHAPTER 6: TOOLS AND TECHNOLOGY USED
# ═══════════════════════════════════════════════════════════════
chapter_head(6, 'Tools and Technology Used')

justified(
    'The following table summarizes the complete set of tools and '
    'technologies used in the development of MindCache:'
)

blank()
make_table(
    ['Category', 'Tool / Technology', 'Purpose'],
    [
        ['Language', 'Python 3.10+', 'Core system implementation'],
        ['Web Framework', 'FastAPI', 'REST API backend, async request handling'],
        ['Database', 'SQLite (SQLAlchemy)', 'Persistent storage for tree, memories, embeddings'],
        ['LLM Runtime', 'Ollama', 'Local inference server for LLMs'],
        ['LLM Models', 'Qwen 1.7B/4B, Gemma', 'Extraction, restructuring, analysis, refinement'],
        ['Embeddings', 'all-MiniLM-L6-v2', 'Vector embedding generation'],
        ['NLP', 'Regex, Custom BM25', 'Input classification, keyword scoring'],
        ['Validation', 'Pydantic v2', 'Type-safe JSON schema enforcement'],
        ['Frontend', 'HTML, CSS, JavaScript', 'Web UI for visualization and testing'],
        ['Extension', 'Chrome Manifest V3', 'Passive data capture from browser'],
        ['Visualization', 'Canvas 2D API', 'Interactive graph with pan/zoom'],
        ['Version Control', 'Git + GitHub', 'Source code management'],
        ['Fine-Tuning', 'Custom pipeline + JSON', 'Supervised training data curation'],
        ['Testing', 'Custom eval framework', 'LongMemEval benchmark integration'],
    ]
)
table_caption('Table 6.1: Tools and Technologies Used')

# ═══════════════════════════════════════════════════════════════
#  CHAPTER 7: CONCLUSION AND FUTURE SCOPE
# ═══════════════════════════════════════════════════════════════
chapter_head(7, 'Conclusion and Future Scope')

section_head('7.1 Conclusion')

justified(
    'MindCache demonstrates that the predominant approach to personal AI '
    'memory systems \u2014 flat vector database storage with brute-force '
    'semantic search \u2014 is fundamentally insufficient for managing the '
    'hierarchical, temporal, and relational nature of real-world knowledge. '
    'By implementing a self-organizing hierarchical topic tree with '
    'autonomous graph maintenance, MindCache achieves a qualitative leap '
    'in how AI systems can structure and retrieve personal knowledge.'
)

justified(
    'The key engineering contributions of this project are: (1) a tri-state '
    'Input Denoiser that reduces LLM token usage by up to 60% through '
    'intelligent preprocessing; (2) a 1530-line tree reorganization engine '
    'that autonomously maintains graph integrity through multi-phase '
    'structural grooming; (3) a Decision State Analyzer that introduces '
    'temporal reasoning into personal knowledge management; and (4) a '
    '5-phase adaptive retrieval pipeline that combines vector and '
    'keyword-based scoring for precise hierarchical search.'
)

section_head('7.2 Results and Evaluation')

justified(
    'The system was evaluated using a custom benchmark derived from the '
    'LongMemEval framework, which tests the ability of a memory system '
    'to accurately retrieve specific information from long-term '
    'conversational context. MindCache achieved the following results:'
)

blank()
make_table(
    ['Metric', 'Score', 'Notes'],
    [
        ['Retrieval Accuracy', '92.16% (47/51)', 'Complex long-term memory queries'],
        ['Token Reduction', 'Up to 60%', 'Measured on mixed code/log/text inputs'],
        ['Knowledge Graph Size', '24 roots, 750+ topics', 'Autonomously organized from raw input'],
    ]
)
table_caption('Table 7.1: LongMemEval Benchmark Results')

justified('Comparison with existing systems:', bold=True, first_indent=False)
blank()
make_table(
    ['System', 'Graph Structure', 'Self-Organizing', 'Temporal Reasoning', 'Retrieval Strategy'],
    [
        ['Standard RAG', 'Flat (Vector DB)', 'No', 'No', 'Brute-force similarity'],
        ['MemPalace', 'Static spatial', 'No', 'Partial', 'Closet-first + fallback'],
        ['MindCache', 'Dynamic hierarchical', 'Yes (autonomous)', 'Yes (Decision Analyzer)', '5-phase adaptive hybrid'],
    ]
)
table_caption('Table 7.2: Comparison with Existing Systems')

section_head('7.3 Future Scope')

justified('Several directions for future development have been identified:', first_indent=False)

bullet('Multi-User Support: ', 'Extending MindCache to support collaborative knowledge graphs where multiple users contribute to and query a shared knowledge base, with access control and attribution tracking.')
bullet('Real-Time Streaming Ingestion: ', 'Replacing the current batch-based ingestion with a real-time streaming pipeline using WebSockets, enabling sub-second memory extraction from live conversations.')
bullet('Cross-Modal Memory: ', 'Extending the memory extraction pipeline to support non-text modalities including images, audio, and structured data.')
bullet('Federated Learning for Privacy: ', 'Implementing federated fine-tuning where multiple MindCache instances can collaboratively improve the extraction model without sharing raw personal data.')
bullet('Advanced Graph Neural Networks: ', 'Replacing embedding-based similarity scoring with GNNs that can learn structural patterns for more accurate retrieval and reorganization.')
bullet('Open-Source Release: ', 'Packaging MindCache as a one-command Docker deployment for public release on GitHub, with comprehensive documentation and contribution guidelines.')

# ═══════════════════════════════════════════════════════════════
#  REFERENCES
# ═══════════════════════════════════════════════════════════════
doc.add_page_break()
blank(2)
center('REFERENCES', size=20, bold=True)
blank(1)

refs = [
    '[1] Bush, V. (1945). \u201cAs We May Think.\u201d The Atlantic Monthly, 176(1), 101-108.',
    '[2] Robertson, S., & Zaragoza, H. (2009). \u201cThe Probabilistic Relevance Framework: BM25 and Beyond.\u201d Foundations and Trends in Information Retrieval, 3(4), 333-389.',
    '[3] Singhal, A. (2012). \u201cIntroducing the Knowledge Graph.\u201d Google Official Blog.',
    '[4] Vrande\u010di\u0107, D., & Kr\u00f6tzsch, M. (2014). \u201cWikidata: A Free Collaborative Knowledgebase.\u201d Communications of the ACM, 57(10), 78-85.',
    '[5] Vaswani, A., et al. (2017). \u201cAttention Is All You Need.\u201d NeurIPS.',
    '[6] Devlin, J., et al. (2019). \u201cBERT: Pre-training of Deep Bidirectional Transformers.\u201d NAACL-HLT.',
    '[7] Reimers, N., & Gurevych, I. (2019). \u201cSentence-BERT: Sentence Embeddings using Siamese BERT-Networks.\u201d EMNLP.',
    '[8] Karpukhin, V., et al. (2020). \u201cDense Passage Retrieval for Open-Domain QA.\u201d EMNLP.',
    '[9] Khattab, O., & Zaharia, M. (2020). \u201cColBERT: Efficient and Effective Passage Search.\u201d SIGIR.',
    '[10] Brown, T., et al. (2020). \u201cLanguage Models are Few-Shot Learners.\u201d NeurIPS.',
    '[11] Touvron, H., et al. (2023). \u201cLLaMA: Open and Efficient Foundation Language Models.\u201d arXiv preprint.',
    '[12] Khattab, O., et al. (2023). \u201cDSPy: Compiling Declarative Language Model Calls.\u201d arXiv preprint.',
    '[13] Liu, J. (2023). \u201cInstructor: Structured Extraction with LLMs.\u201d GitHub Repository.',
    '[14] Bai, J., et al. (2024). \u201cQwen Technical Report.\u201d arXiv preprint.',
    '[15] MemPalace Contributors. (2025). \u201cMemPalace: A Spatial Memory System for AI Assistants.\u201d GitHub Repository.',
]

for ref in refs:
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.left_indent = Cm(1.27)
    p.paragraph_format.first_line_indent = Cm(-1.27)
    p.paragraph_format.line_spacing = 1.5
    run = p.add_run(ref)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(11)

# ═══════════════════════════════════════════════════════════════
#  SAVE
# ═══════════════════════════════════════════════════════════════
output = r'e:\MindCache\MindCache_Report_v2.docx'
doc.save(output)
print(f'Report saved: {output}')
print(f'Size: {os.path.getsize(output) / 1024:.1f} KB')
