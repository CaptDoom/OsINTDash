# Drishya: Tactical OSINT Dashboard User Guide

Welcome to the operator's manual for the **Drishya Geopolitical Telemetry and Collaborative Announce Board**. This document outlines how to use the dashboard interfaces, generate AI summaries, ingest custom intelligence documents, and collaborate via ephemeral shared notes.

---

## 1. Tactical Dashboard Navigation

The Drishya interface is divided into key monitoring screens accessible via the left navigation sidebar:
1. **D3 Live Map**: Click geographical boundaries or monitoring sensors on the live map to query weather anomalies and recent news.
2. **Latest Alerts**: On-screen feed displaying critical security warnings in real-time.
3. **OSINT Archives**: Lookup and filter archived geopolitical incident logs.
4. **AI Summarizer**: Strategic fusion control deck (New Component).
5. **Shared Notes**: Ephemeral announcement feed (New Component).

---

## 2. Time-Based AI Summarizer & Document Ingestion
The **AI Summarizer** enables strategic planning by summarizing geopolitical trends over historical periods, augmented with your own intelligence uploads.

### Step-by-Step Instructions:
1. Click **AI Summarizer** on the sidebar.
2. Select a **Target Entity** (e.g., *China*, *Pakistan*, *Taiwan*) in the configuration control panel.
3. Choose a target timeframe: **1-Month Summary**, **6-Month Summary**, or **1-Year Summary**.
4. **Scrape Web Links (Optional)**: In the **External Links** input, type or paste the URLs of online reports (comma-separated). The scraper will automatically extract raw text content on-the-fly.
5. **Ingest Documents (Optional)**: Drag and drop or browse to upload local reports. Supported formats:
   - **PDF (`.pdf`)**: Parses and extracts text content from document streams.
   - **Word (`.docx`)**: Extract paragraph elements from document files.
   - **Text (`.txt`)**: Reads unicode characters directly.
6. Click **Generate AI Briefing**.
7. The system combines your uploaded files, web links, and matching database news articles into a prompt, outputting a high-fidelity intelligence report containing:
   - **Executive Summary** (Big picture strategic takeaway)
   - **Core Analysis** (Divided into Military & Defense, Economic & Financial, Political & Diplomatic, and Social/Technology sectors)
   - **Strategic Implications & Indicators** (Risk markers and next steps)

---

## 3. Ephemeral Shared Notes Board
The **Shared Notes** board is a collaborative whiteboard for operators to share critical tactical alerts that self-destruct after **24 hours**.

### Posting Announcements:
1. Click **Shared Notes** on the sidebar.
2. Write your announcement in the text editor.
3. Enter your identifier in the **Author** field (defaults to your logged-in username).
4. Click **Pin to Shared Feed**. The card will immediately render at the top of the board.

### Key Operations:
- **Countdown Timer**: Each note displays a ticking countdown (e.g., `18h 32m remaining`). Once the timer reaches `0h 0m`, the note is automatically deleted from the cache database.
- **Manual Dismissal**: Administrators or operators can dismiss cards instantly by clicking the trash icon (`delete`) on the top-right of the note card.

---

## 4. Collaborative "Pin to Notes" Integration
You can broadcast items directly from live news telemetry feeds to the Shared Notes feed with a single click.

### Where to Pin:
- **Country News Dossier**: Select a country on the Live Map. On any news card in the dossier panel, click the **Pin to Notes** button at the card footer.
- **Latest Alerts Feed**: Locate an alert in the sidebar stream, hover over the alert, and click the announcement icon (`campaign`) in the upper-right corner.
- **OSINT Archives Card**: On any query result in the archives, click the **Pin** button next to the incident metadata.

This inserts a pre-formatted intelligence card onto the Shared Notes feed containing the article title, department, description, and source link.
