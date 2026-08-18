"""Web-based ClipChamp-style video editor GUI for devbits clipvideo.

Launches a local HTTP server and opens the browser. The frontend uses
native <video> playback for performance; the backend handles export via cv2.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import unquote

import cv2
from PIL import Image

# ---------------------------------------------------------------------------
# HTML / CSS / JS – embedded as a single-page app
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Divbits.ClipVideo</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB4bWxucz0naHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmcnIHZpZXdCb3g9JzAgMCA2NCA2NCc+PGRlZnM+PGxpbmVhckdyYWRpZW50IGlkPSdnJyB4MT0nMCcgeTE9JzAnIHgyPScxJyB5Mj0nMSc+PHN0b3Agb2Zmc2V0PScwJyBzdG9wLWNvbG9yPScjN2M1Y2ZjJy8+PHN0b3Agb2Zmc2V0PScxJyBzdG9wLWNvbG9yPScjMDBkNGZmJy8+PC9saW5lYXJHcmFkaWVudD48L2RlZnM+PHJlY3Qgd2lkdGg9JzY0JyBoZWlnaHQ9JzY0JyByeD0nMTUnIGZpbGw9J3VybCgjZyknLz48cGF0aCBkPSdNMjUgMTkgTDQ3IDMyIEwyNSA0NSBaJyBmaWxsPScjZmZmJy8+PC9zdmc+Cg==">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
/* ── Reset & Base ──────────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:14px}
body{
  font-family:'Inter',system-ui,sans-serif;
  background:#09090e;
  color:#e0e0f0;
  height:100vh;
  overflow:hidden;
  display:flex;flex-direction:column;
  user-select:none;
}

/* ── Top Bar ───────────────────────────────────────────────── */
.topbar{
  display:flex;align-items:center;justify-content:space-between;
  padding:0 20px;height:52px;min-height:52px;
  background:#0f0f18;
  border-bottom:1px solid rgba(255,255,255,.06);
  z-index:100;
}
.topbar .logo{
  font-weight:700;font-size:1.15rem;
  background:linear-gradient(135deg,#7c5cfc,#00d4ff);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.topbar .filename{color:#8888aa;font-size:.85rem;margin-left:16px}
.topbar-actions{display:flex;gap:8px;align-items:center}

/* ── Buttons ───────────────────────────────────────────────── */
.btn{
  padding:7px 16px;border-radius:8px;border:none;cursor:pointer;
  font-family:inherit;font-weight:600;font-size:.82rem;
  transition:all .15s ease;display:inline-flex;align-items:center;gap:6px;
}
.btn:active{transform:scale(.96)}
.btn:disabled{opacity:.45;cursor:not-allowed;pointer-events:none}
.btn-ghost{background:rgba(255,255,255,.06);color:#c0c0da}
.btn-ghost:hover{background:rgba(255,255,255,.12)}
.btn-primary{
  background:linear-gradient(135deg,#7c5cfc,#5c3cd6);color:#fff;
}
.btn-primary:hover{
  background:linear-gradient(135deg,#8d6eff,#6d4de6);box-shadow:0 4px 20px rgba(124,92,252,.35);
}
.btn-danger{background:rgba(255,60,60,.15);color:#ff6b6b}
.btn-danger:hover{background:rgba(255,60,60,.25)}

/* ── Main Layout ───────────────────────────────────────────── */
.main{
  flex:1;display:flex;flex-direction:row;overflow:hidden;min-height:0;
}

/* ── Left Sidebar (Media Library) ──────────────────────────── */
.sidebar{
  width:280px;background:#0d0d14;border-right:1px solid rgba(255,255,255,.06);
  display:flex;flex-direction:column;padding:16px;flex-shrink:0;
}
.sidebar h3{
  font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:1px;
  color:#666688;margin-bottom:12px;
}
.media-list{
  flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:10px;
  padding-right:4px;
}
/* Scrollbars */
.media-list::-webkit-scrollbar, .timeline-track-wrapper::-webkit-scrollbar{
  width:6px;height:6px;
}
.media-list::-webkit-scrollbar-track, .timeline-track-wrapper::-webkit-scrollbar-track{
  background:transparent;
}
.media-list::-webkit-scrollbar-thumb, .timeline-track-wrapper::-webkit-scrollbar-thumb{
  background:rgba(255,255,255,.08);border-radius:3px;
}
.media-list::-webkit-scrollbar-thumb:hover, .timeline-track-wrapper::-webkit-scrollbar-thumb:hover{
  background:rgba(255,255,255,.16);
}

.media-item{
  display:flex;align-items:center;gap:10px;
  background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.06);
  border-radius:8px;padding:8px;transition:all 0.2s ease;
  cursor:grab;
}
.media-item:hover{
  background:rgba(255,255,255,0.05);border-color:rgba(124,92,252,0.3);
}
.media-item.dragging-media{opacity:.4}
.media-thumb{
  width:50px;height:36px;background:rgba(0,0,0,0.3);
  border-radius:4px;display:flex;align-items:center;justify-content:center;
  font-size:1.1rem;color:#7c5cfc;flex-shrink:0;
}
.media-info{flex:1;min-width:0}
.media-name{
  font-size:0.8rem;font-weight:500;color:#e0e0f0;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.media-duration{font-size:0.7rem;color:#8888aa;margin-top:2px}
.media-add-btn{
  width:24px;height:24px;border-radius:50%;border:none;
  background:#7c5cfc;color:#fff;font-size:1.1rem;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;transition:all 0.15s;
}
.media-add-btn:hover{background:#8d6eff;transform:scale(1.1)}
.media-del-btn{
  width:24px;height:24px;border-radius:50%;border:none;
  background:rgba(255,60,60,.14);color:#ff6b6b;font-size:.85rem;line-height:1;
  display:flex;align-items:center;justify-content:center;
  cursor:pointer;transition:all 0.15s;flex-shrink:0;
}
.media-del-btn:hover{background:rgba(255,60,60,.3);transform:scale(1.1)}
/* Sidebar drop-target highlight */
.sidebar.drag-over{
  background:rgba(124,92,252,.08);
  outline:2px dashed rgba(124,92,252,.45);outline-offset:-6px;
}

/* ── Content Area (Right side) ─────────────────────────────── */
.content-area{
  flex:1;display:flex;flex-direction:column;overflow:hidden;
}

/* ── Preview Area ──────────────────────────────────────────── */
.preview-area{
  flex:1;display:flex;align-items:center;justify-content:center;
  background:#05050b;position:relative;min-height:0;
  overflow:hidden;
}
.preview-area video{
  max-width:100%;max-height:100%;object-fit:contain;
  border-radius:4px;
}
.no-video{color:#555;font-size:1.1rem;text-align:center;line-height:2}

/* ── Playback Controls ─────────────────────────────────────── */
.controls-bar{
  display:flex;align-items:center;gap:12px;
  padding:10px 20px;
  background:#0c0c16;
  border-top:1px solid rgba(255,255,255,.04);
  border-bottom:1px solid rgba(255,255,255,.04);
}
.controls-bar .time{
  font-variant-numeric:tabular-nums;font-size:.82rem;color:#888;
  min-width:140px;
}
.play-btn{
  width:36px;height:36px;border-radius:50%;border:none;cursor:pointer;
  background:linear-gradient(135deg,#7c5cfc,#5c3cd6);
  color:#fff;font-size:14px;
  display:flex;align-items:center;justify-content:center;
  transition:all .15s ease;flex-shrink:0;
}
.play-btn:hover{box-shadow:0 0 16px rgba(124,92,252,.5);transform:scale(1.08)}
.play-btn:active{transform:scale(.95)}

.step-btn{
  width:28px;height:28px;border-radius:6px;border:none;cursor:pointer;
  background:rgba(255,255,255,.06);color:#aaa;font-size:12px;
  display:flex;align-items:center;justify-content:center;
  transition:all .15s ease;flex-shrink:0;
}
.step-btn:hover{background:rgba(255,255,255,.12);color:#fff}

/* Speed control */
.speed-group{display:flex;align-items:center;gap:6px;margin-left:auto}
.speed-group label{font-size:.78rem;color:#666}
.speed-select{
  background:#1a1a35;color:#c0c0da;border:1px solid rgba(255,255,255,.1);
  border-radius:6px;padding:4px 8px;font-family:inherit;font-size:.8rem;
  cursor:pointer;
}
.speed-select:focus{outline:none;border-color:#7c5cfc}

/* ── Timeline ──────────────────────────────────────────────── */
.timeline-section{
  min-height:180px;max-height:240px;
  background:#0a0a14;
  border-top:1px solid rgba(255,255,255,.06);
  display:flex;flex-direction:column;
  padding:0;
}

/* Toolbar above timeline */
.timeline-toolbar{
  display:flex;align-items:center;gap:6px;
  padding:8px 16px;
  border-bottom:1px solid rgba(255,255,255,.04);
}
.timeline-toolbar .btn{font-size:.75rem;padding:5px 12px}

/* Timeline track */
.timeline-track-wrapper{
  flex:1;overflow-x:auto;overflow-y:hidden;position:relative;
  padding:0 16px;
}

.timeline-content{
  position:relative;height:100%;min-height:100px;
  padding:8px 0;
}

.timeline-ruler{
  height:20px;position:relative;
  border-bottom:1px solid rgba(255,255,255,.06);
  margin-bottom:8px;
}

.timeline-track{
  position:relative;height:calc(100% - 28px);
  background:rgba(255,255,255,0.01);
  border-radius:8px;
  border:1px dashed rgba(255,255,255,0.05);
}

.ruler-mark{
  position:absolute;top:0;font-size:.65rem;color:#555577;
  transform:translateX(-50%);
  border-left:1px solid rgba(255,255,255,0.08);
  padding-left:3px;
  height:100%;
}

/* Individual clip block */
.clip-block{
  position:absolute;top:4px;bottom:4px;
  background:linear-gradient(180deg,#2a2a55 0%,#1e1e45 100%);
  border:2px solid transparent;
  border-radius:8px;
  cursor:grab;
  transition:border-color .15s,box-shadow .15s,opacity .15s;
  display:flex;align-items:center;justify-content:center;
  overflow:hidden;
}
.clip-block.dragging{opacity:.3;cursor:grabbing}
.clip-block.drag-over-left{border-left:3px solid #7c5cfc}
.clip-block.drag-over-right{border-right:3px solid #7c5cfc}

/* Floating ghost while dragging a clip */
.clip-drag-ghost{
  position:fixed;pointer-events:none;z-index:9999;
  border-radius:8px;border:2px solid #7c5cfc;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 8px 32px rgba(124,92,252,.5);
  opacity:.85;transform:scale(1.04);
  font-size:.75rem;font-weight:600;color:rgba(255,255,255,.9);
  padding:0 12px;white-space:nowrap;
}

/* Timeline drop indicator for media library drag */
.timeline-drop-indicator{
  position:absolute;top:0;bottom:0;width:3px;
  background:#7c5cfc;z-index:20;border-radius:2px;
  pointer-events:none;
  box-shadow:0 0 8px rgba(124,92,252,.6);
}
.clip-block:hover{border-color:rgba(124,92,252,.4)}
.clip-block.selected{
  border-color:#7c5cfc;
  box-shadow:0 0 12px rgba(124,92,252,.3);
}

.clip-block .clip-label{
  font-size:.75rem;font-weight:600;color:rgba(255,255,255,.8);
  text-align:center;pointer-events:none;z-index:2;
  padding:0 8px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.clip-block .clip-label small{font-weight:400;opacity:.6;display:block;margin-top:2px}

/* Trim handles */
.trim-handle{
  position:absolute;top:0;width:8px;height:100%;
  cursor:col-resize;z-index:5;
  transition:background .15s;
}
.trim-handle:hover,.trim-handle.active{background:rgba(124,92,252,.6)}
.trim-handle.left{left:0;border-radius:6px 0 0 6px}
.trim-handle.right{right:0;border-radius:0 6px 6px 0}

/* Playhead on timeline */
.playhead{
  position:absolute;top:0;bottom:0;width:2px;
  background:#ff4466;z-index:10;
  pointer-events:none;
}
.playhead::before{
  content:'';position:absolute;top:0;left:-5px;
  width:12px;height:12px;
  background:#ff4466;border-radius:50%;
}

/* ── Export Modal ───────────────────────────────────────────── */
.modal-overlay{
  position:fixed;inset:0;background:rgba(0,0,0,.7);
  backdrop-filter:blur(8px);z-index:1000;
  display:flex;align-items:center;justify-content:center;
  opacity:0;pointer-events:none;
  transition:opacity .25s ease;
}
.modal-overlay.show{opacity:1;pointer-events:auto}
.modal{
  background:linear-gradient(180deg,#1a1a38,#141430);
  border:1px solid rgba(255,255,255,.08);
  border-radius:16px;padding:32px;min-width:380px;
  box-shadow:0 24px 80px rgba(0,0,0,.6);
  transform:translateY(20px);transition:transform .25s ease;
}
.modal-overlay.show .modal{transform:translateY(0)}
.modal h2{font-size:1.1rem;margin-bottom:20px;font-weight:600}
.modal label{display:block;font-size:.82rem;color:#888;margin-bottom:4px;margin-top:14px}
.modal select,.modal input[type=text]{
  width:100%;padding:8px 12px;
  background:#0d0d1a;border:1px solid rgba(255,255,255,.1);
  border-radius:8px;color:#e0e0f0;font-family:inherit;font-size:.85rem;
}
.modal select:focus,.modal input:focus{outline:none;border-color:#7c5cfc}
.modal .modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:24px}

/* Progress bar */
.progress-wrap{
  margin-top:16px;height:6px;background:rgba(255,255,255,.06);
  border-radius:3px;overflow:hidden;display:none;
}
.progress-wrap.show{display:block}
.progress-bar{height:100%;width:0;background:linear-gradient(90deg,#7c5cfc,#00d4ff);border-radius:3px;transition:width .3s ease}

/* ── Drop zone ─────────────────────────────────────────────── */
.drop-zone{
  position:absolute;inset:0;
  display:flex;align-items:center;justify-content:center;
  border:2px dashed rgba(124,92,252,.4);
  border-radius:12px;margin:20px;
  background:rgba(124,92,252,.05);
  z-index:50;opacity:0;pointer-events:none;
  transition:opacity .2s;
}
.drop-zone.active{opacity:1;pointer-events:auto}
.drop-zone span{font-size:1.1rem;color:#7c5cfc;font-weight:600}

/* ── Toast ─────────────────────────────────────────────────── */
.toast{
  position:fixed;bottom:24px;right:24px;
  padding:12px 20px;border-radius:10px;
  font-size:.85rem;font-weight:500;
  background:linear-gradient(135deg,#1e1e45,#2a2a55);
  border:1px solid rgba(124,92,252,.3);
  color:#e0e0f0;z-index:2000;
  transform:translateY(80px);opacity:0;
  transition:all .3s ease;
  box-shadow:0 8px 32px rgba(0,0,0,.4);
}
.toast.show{transform:translateY(0);opacity:1}

/* ── Keyboard hint ─────────────────────────────────────────── */
kbd{
  display:inline-block;padding:1px 6px;
  background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);
  border-radius:4px;font-size:.7rem;font-family:inherit;color:#888;
  margin-left:4px;
}

/* ── Context Menu ──────────────────────────────────────────── */
.context-menu{
  position:fixed;z-index:3000;min-width:172px;
  background:#16162b;border:1px solid rgba(255,255,255,.1);
  border-radius:10px;padding:6px;
  box-shadow:0 12px 40px rgba(0,0,0,.55);
  font-size:.82rem;user-select:none;
}
.ctx-item{
  display:flex;align-items:center;gap:9px;
  padding:8px 10px;border-radius:6px;cursor:pointer;color:#d0d0e4;
}
.ctx-item:hover{background:rgba(124,92,252,.2)}
.ctx-item.danger{color:#ff6b6b}
.ctx-item.danger:hover{background:rgba(255,60,60,.18)}
.ctx-item kbd{margin-left:auto}
.ctx-sep{height:1px;background:rgba(255,255,255,.08);margin:6px 4px}
.ctx-label{font-size:.66rem;color:#777799;text-transform:uppercase;letter-spacing:.6px;padding:6px 10px 2px}
.ctx-speeds{display:flex;flex-wrap:wrap;gap:5px;padding:4px 8px 6px}
.ctx-speed{
  padding:4px 9px;border-radius:5px;background:rgba(255,255,255,.06);
  color:#c0c0da;cursor:pointer;font-size:.74rem;transition:all .12s;
}
.ctx-speed:hover{background:rgba(124,92,252,.35)}
.ctx-speed.active{background:#7c5cfc;color:#fff}

/* ── Responsive ────────────────────────────────────────────── */
@media(max-width:850px){
  .main{flex-direction:column}
  .sidebar{width:100%;height:180px;border-right:none;border-bottom:1px solid rgba(255,255,255,.06)}
}
</style>
</head>
<body>

<!-- Top Bar -->
<header class="topbar">
  <div style="display:flex;align-items:center">
    <span class="logo">✂ Divbits.ClipVideo</span>
  </div>
  <div class="topbar-actions">
    <button class="btn btn-primary" onclick="showExportModal()">⬇ Export</button>
  </div>
</header>

<!-- Main Layout -->
<div class="main">
  <!-- Sidebar (Media Library) -->
  <aside class="sidebar" id="sidebar">
    <h3>Media Library</h3>
    <button class="btn btn-primary" onclick="openFile()" style="margin-bottom: 12px; justify-content: center; width: 100%;">
      📂 Import media
    </button>
    <div class="media-list" id="mediaList">
      <!-- Media Items go here -->
    </div>
  </aside>

  <!-- Content Area -->
  <div class="content-area">
    <!-- Preview Area -->
    <div class="preview-area" id="previewArea">
      <video id="video" preload="auto"></video>
      <div class="no-video" id="noVideo">
        Drop a video here or click <strong>Import media</strong> to begin<br>
        <small style="opacity:.5">Supports MP4, AVI, MOV, MKV, WebM</small>
      </div>
      <div class="drop-zone" id="dropZone"><span>Drop video file here</span></div>
    </div>

    <!-- Playback Controls -->
    <div class="controls-bar">
      <button class="step-btn" onclick="seekTimeline(0)" title="Jump to start">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><rect x="5" y="5" width="2.6" height="14" rx="1"/><path d="M20 5v14L9.5 12z"/></svg>
      </button>
      <button class="step-btn" onclick="stepFrame(-1)" title="Previous frame">⏮</button>
      <button class="play-btn" id="playBtn" onclick="togglePlay()" title="Play/Pause (Space)">▶</button>
      <button class="step-btn" onclick="stepFrame(1)" title="Next frame">⏭</button>
      <button class="step-btn" onclick="seekTimeline(getTotalDuration())" title="Jump to end">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M4 5v14l10.5-7z"/><rect x="16.4" y="5" width="2.6" height="14" rx="1"/></svg>
      </button>
      <span class="time" id="timeDisplay">0:00.000 / 0:00.000</span>

      <div class="speed-group">
        <label>Speed</label>
        <select class="speed-select" id="speedSelect" onchange="setSpeed(this.value)">
          <option value="0.25">0.25×</option>
          <option value="0.5">0.5×</option>
          <option value="0.75">0.75×</option>
          <option value="1" selected>1×</option>
          <option value="1.25">1.25×</option>
          <option value="1.5">1.5×</option>
          <option value="2">2×</option>
          <option value="4">4×</option>
        </select>
      </div>
    </div>

    <!-- Timeline Section -->
    <div class="timeline-section">
      <div class="timeline-toolbar">
        <button class="btn btn-ghost" onclick="splitAtPlayhead()" title="Split (S)">✂ Split<kbd>S</kbd></button>
        <button class="btn btn-danger" onclick="deleteSelected()" title="Delete (Del)">🗑 Delete<kbd>Del</kbd></button>

        <div style="flex:1"></div>
        <span style="font-size:.75rem;color:#777799" id="clipInfo"></span>
      </div>
      <div class="timeline-track-wrapper" id="trackWrapper">
        <div class="timeline-content" id="timelineContent">
          <div class="timeline-ruler" id="ruler"></div>
          <div class="timeline-track" id="track"></div>
          <div class="playhead" id="playhead"></div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Export Modal -->
<div class="modal-overlay" id="exportModal">
  <div class="modal">
    <h2>Export Video</h2>
    <label>Format</label>
    <select id="exportFormat">
      <option value="mp4">MP4 (.mp4)</option>
      <option value="avi">AVI (.avi)</option>
      <option value="webm">WebM (.webm)</option>
      <option value="gif">GIF (.gif)</option>
    </select>
    <label>Resolution</label>
    <select id="exportResolution">
      <option value="3840">4K (3840×2160)</option>
      <option value="2560">1440p (2560×1440)</option>
      <option value="1920" selected>1080p (1920×1080)</option>
      <option value="1280">720p (1280×720)</option>
      <option value="854">480p (854×480)</option>
      <option value="0">Original</option>
    </select>
    <label>Filename</label>
    <input type="text" id="exportFilename" value="output">
    <div class="progress-wrap" id="progressWrap">
      <div class="progress-bar" id="progressBar"></div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="hideExportModal()">Cancel</button>
      <button class="btn btn-primary" id="exportBtn" onclick="doExport()">Export</button>
    </div>
  </div>
</div>

<!-- Confirm Modal -->
<div class="modal-overlay" id="confirmModal">
  <div class="modal" style="min-width:340px">
    <h2 id="confirmTitle">Confirm</h2>
    <p id="confirmMessage" style="color:#b0b0c8;font-size:.9rem;line-height:1.5;margin-top:4px"></p>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="hideConfirm()">Cancel</button>
      <button class="btn btn-danger" id="confirmOk" onclick="runConfirm()">Delete</button>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast" id="toast"></div>

<!-- Hidden file input -->
<input type="file" id="fileInput" accept="video/*" multiple style="display:none" onchange="handleFileInput(event)">

<script>
// ── State ──────────────────────────────────────────────────────
const video = document.getElementById('video');
let mediaLibrary = []; // {id, src, name, duration}
let clips = []; // {id, src, name, startTime, endTime, speed, duration, hue}
let selectedClipId = null;
let activeClipIndex = 0;
let timelineTime = 0;
let clipIdCounter = 0;
let hueCounter = 0; // stable color assignment
let isChangingSource = false;
let isScrubbing = false;
let wasPlayingBeforeScrub = false;

let PX_PER_SEC = 80;   // timeline scale (px per second); changed by Ctrl+wheel zoom
const PX_PER_SEC_MIN = 10;
const PX_PER_SEC_MAX = 800;
const TIMELINE_PAD = 16; // matches .timeline-track-wrapper horizontal padding
const CLIP_GAP = 3; // px gap between clips

// ── Duration Helper ────────────────────────────────────────────
function getMediaDuration(src) {
  return new Promise((resolve) => {
    const tempVideo = document.createElement('video');
    tempVideo.src = src;
    tempVideo.preload = 'metadata';
    tempVideo.onloadedmetadata = () => {
      resolve(tempVideo.duration);
    };
    tempVideo.onerror = () => {
      resolve(0);
    };
  });
}

// ── Media Library Operations ──────────────────────────────────
function addMediaToLibrary(src, name, duration, autoAddToTimeline = false) {
  // Normalize src for dedup (resolve to absolute URL)
  const a = document.createElement('a');
  a.href = src;
  const normalizedSrc = a.href;
  if (mediaLibrary.some(item => {
    const b = document.createElement('a');
    b.href = item.src;
    return b.href === normalizedSrc;
  })) {
    if (autoAddToTimeline) {
      const existing = mediaLibrary.find(item => {
        const b = document.createElement('a');
        b.href = item.src;
        return b.href === normalizedSrc;
      });
      if (existing) addMediaToTimeline(existing);
    }
    return;
  }
  const item = {
    id: ++clipIdCounter,
    src: src,
    name: name,
    duration: duration
  };
  mediaLibrary.push(item);
  renderMediaLibrary();

  if (autoAddToTimeline) {
    addMediaToTimeline(item);
  }
  toast(`Imported "${name}"`);
}

function renderMediaLibrary() {
  const list = document.getElementById('mediaList');
  list.innerHTML = '';

  if (mediaLibrary.length === 0) {
    list.innerHTML = `
      <div style="text-align: center; color: #555577; padding: 24px; font-size: 0.85rem; line-height: 1.5;">
        No media imported.<br>Click "Import media" or drag files here.
      </div>
    `;
    return;
  }

  mediaLibrary.forEach(item => {
    const card = document.createElement('div');
    card.className = 'media-item';
    card.draggable = false; // we use custom mousedown drag

    const thumb = document.createElement('div');
    thumb.className = 'media-thumb';
    thumb.textContent = '📹';

    const info = document.createElement('div');
    info.className = 'media-info';

    const nameEl = document.createElement('div');
    nameEl.className = 'media-name';
    nameEl.textContent = item.name;
    nameEl.title = item.name;

    const duration = document.createElement('div');
    duration.className = 'media-duration';
    duration.textContent = fmtTime(item.duration);

    info.appendChild(nameEl);
    info.appendChild(duration);

    const addBtn = document.createElement('button');
    addBtn.className = 'media-add-btn';
    addBtn.textContent = '+';
    addBtn.title = 'Add to timeline';
    addBtn.onclick = (e) => {
      e.stopPropagation();
      addMediaToTimeline(item);
    };

    const delBtn = document.createElement('button');
    delBtn.className = 'media-del-btn';
    delBtn.textContent = '✕';
    delBtn.title = 'Remove from library';
    delBtn.onclick = (e) => {
      e.stopPropagation();
      showConfirm(`Remove "${item.name}" from the media library?`, () => removeMediaFromLibrary(item.id));
    };

    // Drag from media library to timeline
    card.addEventListener('mousedown', (e) => {
      if (e.target.closest('.media-add-btn') || e.target.closest('.media-del-btn')) return;
      startMediaLibraryDrag(e, item, card);
    });

    card.appendChild(thumb);
    card.appendChild(info);
    card.appendChild(addBtn);
    card.appendChild(delBtn);

    list.appendChild(card);
  });
}

function removeMediaFromLibrary(id) {
  const idx = mediaLibrary.findIndex(m => m.id === id);
  if (idx === -1) return;
  const [removed] = mediaLibrary.splice(idx, 1);
  renderMediaLibrary();
  toast(`Removed "${removed.name}"`);
}

// ── Confirm Modal ──────────────────────────────────────────────
let _confirmCb = null;
function showConfirm(message, onYes, okLabel = 'Delete') {
  _confirmCb = onYes;
  document.getElementById('confirmMessage').textContent = message;
  document.getElementById('confirmOk').textContent = okLabel;
  document.getElementById('confirmModal').classList.add('show');
}
function hideConfirm() {
  document.getElementById('confirmModal').classList.remove('show');
  _confirmCb = null;
}
function runConfirm() {
  const cb = _confirmCb;
  hideConfirm();
  if (cb) cb();
}

function addMediaToTimeline(media, insertAtIndex = -1) {
  const newClip = {
    id: ++clipIdCounter,
    src: media.src,
    name: media.name,
    startTime: 0,
    endTime: media.duration,
    speed: 1,
    duration: media.duration,
    hue: (hueCounter++ * 37 + 230) % 360
  };
  if (insertAtIndex >= 0 && insertAtIndex <= clips.length) {
    clips.splice(insertAtIndex, 0, newClip);
  } else {
    clips.push(newClip);
  }
  selectedClipId = newClip.id;

  if (clips.length === 1) {
    activeClipIndex = 0;
    seekTimeline(0);
  }
  renderTimeline();
  toast(`Added to timeline`);
}

// ── File Input & Upload ────────────────────────────────────────
function openFile() { document.getElementById('fileInput').click(); }
function handleFileInput(e) {
  const files = Array.from(e.target.files);
  files.forEach(file => {
    uploadAndLoad(file);
  });
}

async function uploadAndLoad(file) {
  // Skip files already present in the library (match by name).
  if (mediaLibrary.some(m => m.name === file.name)) {
    toast(`"${file.name}" is already in the library`);
    return;
  }
  const formData = new FormData();
  formData.append('file', file);
  try {
    const resp = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await resp.json();
    if (data.src) {
      const duration = await getMediaDuration(data.src);
      addMediaToLibrary(data.src, file.name, duration);
    }
  } catch(e) {
    toast('Upload failed: ' + e.message, true);
  }
}

// Drag & drop
const previewArea = document.getElementById('previewArea');
const dropZone = document.getElementById('dropZone');
['dragenter','dragover'].forEach(ev => {
  previewArea.addEventListener(ev, e => { e.preventDefault(); dropZone.classList.add('active'); });
});
['dragleave','drop'].forEach(ev => {
  previewArea.addEventListener(ev, e => { e.preventDefault(); dropZone.classList.remove('active'); });
});
previewArea.addEventListener('drop', e => {
  const files = Array.from(e.dataTransfer.files);
  files.forEach(file => {
    if (file.type.startsWith('video/')) {
      uploadAndLoad(file);
    }
  });
});

// Drag & drop onto the Media Library sidebar (import area)
const sidebar = document.getElementById('sidebar');
['dragenter','dragover'].forEach(ev => {
  sidebar.addEventListener(ev, e => { e.preventDefault(); sidebar.classList.add('drag-over'); });
});
['dragleave','drop'].forEach(ev => {
  sidebar.addEventListener(ev, e => {
    e.preventDefault();
    // Ignore dragleave that fires while moving over child elements.
    if (ev === 'dragleave' && e.relatedTarget && sidebar.contains(e.relatedTarget)) return;
    sidebar.classList.remove('drag-over');
  });
});
sidebar.addEventListener('drop', e => {
  const files = Array.from(e.dataTransfer.files);
  files.forEach(file => {
    if (file.type.startsWith('video/')) {
      uploadAndLoad(file);
    }
  });
});

// ── Initial Video Load ─────────────────────────────────────────
if (window.__INITIAL_VIDEO__) {
  const src = window.__INITIAL_VIDEO__;
  const name = decodeURIComponent(src.split('/').pop());
  getMediaDuration(src).then(duration => {
    addMediaToLibrary(src, name, duration, true);
  });
} else {
  renderMediaLibrary();
}

// ── Playback Logic ─────────────────────────────────────────────
function togglePlay() {
  if (clips.length === 0 || isChangingSource) return;
  if (video.paused) {
    video.play().catch(e => console.log("Play interrupted:", e));
    document.getElementById('playBtn').innerHTML = '⏸';
  } else {
    video.pause();
    document.getElementById('playBtn').innerHTML = '▶';
  }
}

video.addEventListener('pause', () => {
  if (!isScrubbing) document.getElementById('playBtn').innerHTML = '▶';
});
video.addEventListener('play', () => {
  document.getElementById('playBtn').innerHTML = '⏸';
});

function stepFrame(dir) {
  if (clips.length === 0 || isChangingSource) return;
  video.pause();
  const fps = 30;
  const clip = clips[activeClipIndex];
  if (clip) {
    const targetTime = Math.max(clip.startTime, Math.min(clip.endTime, video.currentTime + dir / fps));
    video.currentTime = targetTime;
  }
}

function setSpeed(val) {
  const spd = parseFloat(val);
  if (selectedClipId !== null) {
    const clip = clips.find(c => c.id === selectedClipId);
    if (clip) {
      clip.speed = spd;
      if (clips[activeClipIndex].id === selectedClipId) {
        video.playbackRate = spd;
      }
      renderTimeline();
    }
  }
}

// Time display update loop
function updateTimeDisplay() {
  if (clips.length === 0) {
    document.getElementById('timeDisplay').textContent = '0:00.000 / 0:00.000';
    document.getElementById('noVideo').style.display = 'block';
    video.style.display = 'none';
    updatePlayhead();
    requestAnimationFrame(updateTimeDisplay);
    return;
  }

  document.getElementById('noVideo').style.display = 'none';
  video.style.display = 'block';

  if (!video.paused && !isChangingSource) {
    const clip = clips[activeClipIndex];
    if (clip) {
      if (video.currentTime >= clip.endTime) {
        if (activeClipIndex + 1 < clips.length) {
          activeClipIndex++;
          const nextClip = clips[activeClipIndex];
          loadVideoSource(nextClip.src, nextClip.startTime, true);
        } else {
          video.pause();
          seekTimeline(getTotalDuration());
        }
      } else if (video.currentTime < clip.startTime) {
        video.currentTime = clip.startTime;
      }
    }
  }

  if (!isChangingSource) {
    let acc = 0;
    for (let i = 0; i < activeClipIndex; i++) {
      acc += (clips[i].endTime - clips[i].startTime) / clips[i].speed;
    }
    const clip = clips[activeClipIndex];
    if (clip) {
      const clipProgress = (video.currentTime - clip.startTime) / clip.speed;
      timelineTime = acc + Math.max(0, clipProgress);
    }
  }

  const totalDur = getTotalDuration();
  document.getElementById('timeDisplay').textContent =
    `${fmtTime(timelineTime)} / ${fmtTime(totalDur)}`;

  updatePlayhead();
  requestAnimationFrame(updateTimeDisplay);
}

// Start time loop
requestAnimationFrame(updateTimeDisplay);

function fmtTime(t) {
  if (!isFinite(t) || t < 0) return '0:00.000';
  const m = Math.floor(t / 60);
  const s = t % 60;
  return `${m}:${s < 10 ? '0' : ''}${s.toFixed(3)}`;
}

// ── Timeline Sync & Interaction ──────────────────────────────
function getTimelineStartOfClip(index) {
  let acc = 0;
  for (let i = 0; i < index; i++) {
    acc += (clips[i].endTime - clips[i].startTime) / clips[i].speed;
  }
  return acc;
}

function getTotalDuration() {
  let acc = 0;
  for (const clip of clips) {
    acc += (clip.endTime - clip.startTime) / clip.speed;
  }
  return acc;
}

function loadVideoSource(src, seekTime, shouldPlay = false) {
  const tempA = document.createElement('a');
  tempA.href = src;
  const absSrc = tempA.href;
  const currentAbsSrc = video.src ? new URL(video.src, window.location.href).href : '';

  if (currentAbsSrc !== absSrc) {
    isChangingSource = true;
    video.pause();
    video.src = src;
    video.playbackRate = clips[activeClipIndex].speed;
    video.load();

    const onCanPlay = () => {
      video.currentTime = seekTime;
      isChangingSource = false;
      if (shouldPlay) {
        video.play().catch(e => console.log("Play interrupted:", e));
      }
      video.removeEventListener('canplay', onCanPlay);
    };
    video.addEventListener('canplay', onCanPlay);
  } else {
    video.currentTime = seekTime;
    video.playbackRate = clips[activeClipIndex].speed;
    if (shouldPlay && video.paused) {
      video.play().catch(e => console.log("Play interrupted:", e));
    }
  }
}

function seekTimeline(t) {
  const totalDur = getTotalDuration();
  t = Math.max(0, Math.min(totalDur, t));
  timelineTime = t;

  if (clips.length === 0) {
    video.src = '';
    timelineTime = 0;
    return;
  }

  let acc = 0;
  let found = false;
  for (let i = 0; i < clips.length; i++) {
    const clipDur = (clips[i].endTime - clips[i].startTime) / clips[i].speed;
    if (t <= acc + clipDur) {
      activeClipIndex = i;
      const relTime = t - acc;
      const sourceTime = clips[i].startTime + relTime * clips[i].speed;
      const wasPlaying = !video.paused;
      loadVideoSource(clips[i].src, sourceTime, wasPlaying);
      found = true;
      break;
    }
    acc += clipDur;
  }

  if (!found && clips.length > 0) {
    activeClipIndex = clips.length - 1;
    const lastClip = clips[activeClipIndex];
    const wasPlaying = !video.paused;
    loadVideoSource(lastClip.src, lastClip.endTime, wasPlaying);
  }

  updateClipInfo();
}

function selectClip(clipId) {
  selectedClipId = clipId;
  const clip = clips.find(c => c.id === clipId);
  if (clip) {
    document.getElementById('speedSelect').value = clip.speed;
  }
  renderTimeline();
}

// Timeline Click / Scrubbing
function handleTimelineClick(e) {
  if (clips.length === 0) return;
  const content = document.getElementById('timelineContent');
  const rect = content.getBoundingClientRect();
  let x = e.clientX - rect.left;
  x = Math.max(0, Math.min(rect.width, x));
  const t = x / PX_PER_SEC;
  seekTimeline(t);
}

function setupTimelineInteraction() {
  const wrapper = document.getElementById('trackWrapper');

  const onMouseDown = (e) => {
    if (e.button !== 0) return;
    // Ignore clicks on the horizontal scrollbar (below the client area) so
    // dragging the scrollbar doesn't scrub the timeline.
    if (e.target === wrapper) {
      const r = wrapper.getBoundingClientRect();
      if (e.clientY > r.top + wrapper.clientHeight || e.clientX > r.left + wrapper.clientWidth) return;
    }
    if (e.target.closest('.trim-handle') || e.target.closest('.btn-danger')) return;
    // Don't start scrubbing if a drag is starting on a clip block
    if (e.target.closest('.clip-block') && !e.target.closest('.trim-handle')) {
      const clipEl = e.target.closest('.clip-block');
      const clipId = parseInt(clipEl.dataset.clipId);
      selectClip(clipId);
      // Start potential drag
      startClipDrag(e, clipEl, clipId);
      return;
    }

    isScrubbing = true;
    wasPlayingBeforeScrub = !video.paused;
    if (wasPlayingBeforeScrub) {
      video.pause();
    }

    handleTimelineClick(e);

    const onMouseMove = (ev) => {
      if (isScrubbing) {
        handleTimelineClick(ev);
      }
    };

    const onMouseUp = () => {
      isScrubbing = false;
      if (wasPlayingBeforeScrub) {
        video.play().catch(e => console.log("Play interrupted:", e));
      }
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  };

  wrapper.addEventListener('mousedown', onMouseDown);

  // Ctrl/⌘ + wheel (or trackpad pinch) zooms the timeline around the cursor.
  wrapper.addEventListener('wheel', (e) => {
    if (!(e.ctrlKey || e.metaKey)) return; // plain scroll: let the browser scroll
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    zoomTimelineAt(e.clientX, factor);
  }, { passive: false });
}

// Zoom the timeline keeping the time under `clientX` pinned to the cursor.
function zoomTimelineAt(clientX, factor) {
  const wrapper = document.getElementById('trackWrapper');
  const content = document.getElementById('timelineContent');
  const t = (clientX - content.getBoundingClientRect().left) / PX_PER_SEC;

  PX_PER_SEC = Math.max(PX_PER_SEC_MIN, Math.min(PX_PER_SEC_MAX, PX_PER_SEC * factor));

  renderTimeline();
  updatePlayhead();

  const wrapRect = wrapper.getBoundingClientRect();
  wrapper.scrollLeft = Math.max(0, wrapRect.left + TIMELINE_PAD + t * PX_PER_SEC - clientX);
}

// ── Clip Drag & Drop Reorder ──────────────────────────────────
function createDragGhost(text, hue, width, height) {
  const ghost = document.createElement('div');
  ghost.className = 'clip-drag-ghost';
  ghost.textContent = text;
  ghost.style.width = Math.min(width, 200) + 'px';
  ghost.style.height = height + 'px';
  ghost.style.background = `linear-gradient(180deg, hsl(${hue},45%,32%) 0%, hsl(${hue},40%,22%) 100%)`;
  document.body.appendChild(ghost);
  return ghost;
}

function startClipDrag(e, clipEl, clipId) {
  const startX = e.clientX;
  const startY = e.clientY;
  let dragging = false;
  let ghost = null;
  const DRAG_THRESHOLD = 5;
  const srcIdx = clips.findIndex(c => c.id === clipId);
  if (srcIdx === -1) return;
  const clip = clips[srcIdx];

  const onMove = (ev) => {
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (!dragging && Math.abs(dx) + Math.abs(dy) > DRAG_THRESHOLD) {
      dragging = true;
      clipEl.classList.add('dragging');
      const rect = clipEl.getBoundingClientRect();
      ghost = createDragGhost(clip.name, clip.hue, rect.width, rect.height);
    }
    if (!dragging) return;

    // Move ghost
    ghost.style.left = (ev.clientX - 60) + 'px';
    ghost.style.top = (ev.clientY - 20) + 'px';

    // Find which clip we're hovering over
    const allClips = document.querySelectorAll('.clip-block');
    allClips.forEach(el => {
      el.classList.remove('drag-over-left', 'drag-over-right');
    });
    for (const el of allClips) {
      if (el === clipEl) continue;
      const rect = el.getBoundingClientRect();
      const mid = rect.left + rect.width / 2;
      if (ev.clientX >= rect.left && ev.clientX <= rect.right) {
        if (ev.clientX < mid) {
          el.classList.add('drag-over-left');
        } else {
          el.classList.add('drag-over-right');
        }
      }
    }
  };

  const onUp = (ev) => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    clipEl.classList.remove('dragging');
    if (ghost) { ghost.remove(); ghost = null; }

    if (!dragging) {
      // Just a click on a clip — selection already happened on mousedown.
      // Do NOT move the playhead; only clicking the empty timeline scrubs.
      return;
    }

    // Find drop target
    const allClips = document.querySelectorAll('.clip-block');
    let targetIdx = -1;
    let insertAfter = false;
    for (const el of allClips) {
      el.classList.remove('drag-over-left', 'drag-over-right');
      if (el === clipEl) continue;
      const rect = el.getBoundingClientRect();
      const mid = rect.left + rect.width / 2;
      if (ev.clientX >= rect.left && ev.clientX <= rect.right) {
        const tId = parseInt(el.dataset.clipId);
        targetIdx = clips.findIndex(c => c.id === tId);
        insertAfter = ev.clientX >= mid;
        break;
      }
    }

    if (targetIdx !== -1 && targetIdx !== srcIdx) {
      const [removed] = clips.splice(srcIdx, 1);
      let insertIdx = targetIdx;
      if (srcIdx < targetIdx) insertIdx--;
      if (insertAfter) insertIdx++;
      clips.splice(insertIdx, 0, removed);
      selectedClipId = removed.id;
      activeClipIndex = clips.findIndex(c => c.id === removed.id);
      renderTimeline();
      seekTimeline(getTimelineStartOfClip(activeClipIndex));
      toast('Clip moved');
    } else {
      renderTimeline();
    }
  };

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

// ── Media Library Drag to Timeline ────────────────────────────
function startMediaLibraryDrag(e, media, card) {
  const startX = e.clientX;
  const startY = e.clientY;
  let dragging = false;
  let ghost = null;
  let dropIndicator = null;
  const DRAG_THRESHOLD = 5;

  const onMove = (ev) => {
    const dx = ev.clientX - startX;
    const dy = ev.clientY - startY;
    if (!dragging && Math.abs(dx) + Math.abs(dy) > DRAG_THRESHOLD) {
      dragging = true;
      card.classList.add('dragging-media');
      ghost = createDragGhost(media.name, (hueCounter * 37 + 230) % 360, 160, 40);
    }
    if (!dragging) return;

    ghost.style.left = (ev.clientX - 60) + 'px';
    ghost.style.top = (ev.clientY - 20) + 'px';

    // Show drop position on timeline
    const track = document.getElementById('track');
    const trackRect = track.getBoundingClientRect();
    if (ev.clientY >= trackRect.top - 40 && ev.clientY <= trackRect.bottom + 40) {
      if (!dropIndicator) {
        dropIndicator = document.createElement('div');
        dropIndicator.className = 'timeline-drop-indicator';
        document.getElementById('timelineContent').appendChild(dropIndicator);
      }
      const insertIdx = getInsertIndexAtX(ev.clientX);
      const pos = getTimelineStartOfClip(insertIdx) * PX_PER_SEC;
      dropIndicator.style.left = pos + 'px';
    } else if (dropIndicator) {
      dropIndicator.remove();
      dropIndicator = null;
    }
  };

  const onUp = (ev) => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    card.classList.remove('dragging-media');
    if (ghost) { ghost.remove(); ghost = null; }
    if (dropIndicator) { dropIndicator.remove(); dropIndicator = null; }

    if (!dragging) return; // was just a click

    // Check if dropped on timeline area
    const track = document.getElementById('track');
    const trackRect = track.getBoundingClientRect();
    if (ev.clientY >= trackRect.top - 40 && ev.clientY <= trackRect.bottom + 40) {
      const insertIdx = getInsertIndexAtX(ev.clientX);
      addMediaToTimeline(media, insertIdx);
    }
  };

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

function getInsertIndexAtX(clientX) {
  const content = document.getElementById('timelineContent');
  const rect = content.getBoundingClientRect();
  const x = Math.max(0, clientX - rect.left);
  const t = x / PX_PER_SEC;

  let acc = 0;
  for (let i = 0; i < clips.length; i++) {
    const clipDur = (clips[i].endTime - clips[i].startTime) / clips[i].speed;
    const mid = acc + clipDur / 2;
    if (t < mid) return i;
    acc += clipDur;
  }
  return clips.length;
}

document.addEventListener('DOMContentLoaded', setupTimelineInteraction);

// ── Timeline Rendering ─────────────────────────────────────────
function renderTimeline() {
  const track = document.getElementById('track');
  track.innerHTML = '';

  const totalDuration = getTotalDuration();
  const totalWidth = totalDuration * PX_PER_SEC;

  // Sync width of track and content
  const content = document.getElementById('timelineContent');
  const trackWidth = Math.max(wrapperWidth(), totalWidth);
  content.style.width = trackWidth + 'px';
  track.style.width = totalWidth + 'px';

  const totalGap = clips.length > 1 ? (clips.length - 1) * CLIP_GAP : 0;

  let accTime = 0;
  clips.forEach((clip, i) => {
    const clipDur = (clip.endTime - clip.startTime) / clip.speed;
    const left = accTime * PX_PER_SEC + i * CLIP_GAP;
    const w = clipDur * PX_PER_SEC;

    const el = document.createElement('div');
    el.className = 'clip-block' + (clip.id === selectedClipId ? ' selected' : '');
    el.style.left = left + 'px';
    el.style.width = w + 'px';
    el.dataset.clipId = clip.id;

    // Use stable per-clip hue instead of index-based
    const hue = clip.hue !== undefined ? clip.hue : (i * 37 + 230) % 360;
    el.style.background = `linear-gradient(180deg, hsl(${hue},45%,32%) 0%, hsl(${hue},40%,22%) 100%)`;

    const label = document.createElement('div');
    label.className = 'clip-label';
    label.innerHTML = `${clip.name}<small>${clipDur.toFixed(1)}s · ${clip.speed}×</small>`;
    el.appendChild(label);

    const lh = document.createElement('div');
    lh.className = 'trim-handle left';
    lh.addEventListener('mousedown', e => startTrim(e, clip, 'left'));
    el.appendChild(lh);

    const rh = document.createElement('div');
    rh.className = 'trim-handle right';
    rh.addEventListener('mousedown', e => startTrim(e, clip, 'right'));
    el.appendChild(rh);

    el.addEventListener('contextmenu', e => showClipContextMenu(e, clip.id, el));

    track.appendChild(el);
    accTime += clipDur;
  });

  // Account for gaps in total track width
  track.style.width = (totalDuration * PX_PER_SEC + totalGap) + 'px';

  renderRuler();
  updateClipInfo();
}



// Lightweight re-layout of existing clip DOM nodes (no rebuild → smooth dragging)
function layoutClips() {
  const track = document.getElementById('track');
  let accTime = 0;
  clips.forEach((clip, i) => {
    const el = track.querySelector(`.clip-block[data-clip-id="${clip.id}"]`);
    const clipDur = (clip.endTime - clip.startTime) / clip.speed;
    if (el) {
      el.style.left = (accTime * PX_PER_SEC + i * CLIP_GAP) + 'px';
      el.style.width = (clipDur * PX_PER_SEC) + 'px';
      const lbl = el.querySelector('.clip-label');
      if (lbl) lbl.innerHTML = `${clip.name}<small>${clipDur.toFixed(1)}s · ${clip.speed}×</small>`;
    }
    accTime += clipDur;
  });
}

function updateClipInfo() {
  const el = document.getElementById('clipInfo');
  if (selectedClipId === null) { el.textContent = ''; return; }
  const clip = clips.find(c => c.id === selectedClipId);
  if (!clip) { el.textContent = ''; return; }
  const dur = clip.endTime - clip.startTime;
  el.textContent = `Selected: ${fmtTime(clip.startTime)} → ${fmtTime(clip.endTime)} (${dur.toFixed(2)}s @ ${clip.speed}×)`;
}

// Fixed compile issue: get wrapperWidth dynamically in ruler rendering
function renderRuler() {
  const ruler = document.getElementById('ruler');
  ruler.innerHTML = '';
  const totalDuration = getTotalDuration();
  // Pick a step so labels keep at least ~60px apart at the current zoom.
  const minPx = 60;
  const candidates = [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  let step = candidates[candidates.length - 1];
  for (const c of candidates) { if (c * PX_PER_SEC >= minPx) { step = c; break; } }

  for (let t = 0; t <= totalDuration + 1e-9; t += step) {
    const mk = document.createElement('div');
    mk.className = 'ruler-mark';
    mk.style.left = (t * PX_PER_SEC) + 'px';
    mk.textContent = fmtTime(t);
    ruler.appendChild(mk);
  }
}

function wrapperWidth() {
  const wrapper = document.getElementById('trackWrapper');
  return wrapper ? wrapper.clientWidth - 32 : 800;
}

function updatePlayhead() {
  const ph = document.getElementById('playhead');
  ph.style.left = (timelineTime * PX_PER_SEC) + 'px';
}

// ── Trim Handles ───────────────────────────────────────────────
function startTrim(e, clip, side) {
  e.preventDefault();
  e.stopPropagation();
  const startX = e.clientX;
  const origStart = clip.startTime;
  const origEnd = clip.endTime;

  selectedClipId = clip.id;
  const clipIndex = clips.findIndex(c => c.id === clip.id);
  activeClipIndex = clipIndex;

  loadVideoSource(clip.src, side === 'left' ? clip.startTime : clip.endTime);

  // Throttle preview seeks to one per animation frame to avoid decode thrash.
  let pendingSeek = null;
  let rafId = null;
  const flushSeek = () => {
    rafId = null;
    if (pendingSeek !== null && !isChangingSource) {
      video.currentTime = pendingSeek;
      pendingSeek = null;
    }
  };

  const onMove = ev => {
    const dx = ev.clientX - startX;
    const dt = dx / PX_PER_SEC;

    if (side === 'left') {
      clip.startTime = Math.max(0, Math.min(clip.endTime - 0.1, origStart + dt));
      pendingSeek = clip.startTime;
    } else {
      clip.endTime = Math.max(clip.startTime + 0.1, Math.min(clip.duration, origEnd + dt));
      pendingSeek = clip.endTime;
    }
    // Re-layout existing nodes instead of rebuilding the whole timeline.
    layoutClips();
    updateClipInfo();
    if (rafId === null) rafId = requestAnimationFrame(flushSeek);
  };

  const onUp = () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    if (rafId !== null) cancelAnimationFrame(rafId);
    renderTimeline();
    seekTimeline(getTimelineStartOfClip(clipIndex) + (side === 'left' ? 0 : (clip.endTime - clip.startTime) / clip.speed));
  };

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

// ── Clip Operations ────────────────────────────────────────────
function splitAtPlayhead() {
  if (clips.length === 0 || isChangingSource) return;
  const clip = clips[activeClipIndex];
  if (!clip) return;
  splitClipAt(clip.id, video.currentTime);
}

// Split a specific clip at a source-time `t` (seconds within the source video).
function splitClipAt(clipId, t) {
  const idx = clips.findIndex(c => c.id === clipId);
  if (idx === -1) return;
  const clip = clips[idx];
  if (t <= clip.startTime + 0.1 || t >= clip.endTime - 0.1) {
    toast('Move the cut point inside the clip to split');
    return;
  }

  const newClip1 = { ...clip, id: ++clipIdCounter, endTime: t, hue: clip.hue };
  const newClip2 = { ...clip, id: ++clipIdCounter, startTime: t, hue: (hueCounter++ * 37 + 230) % 360 };

  clips.splice(idx, 1, newClip1, newClip2);
  activeClipIndex = idx + 1;
  selectedClipId = newClip2.id;

  renderTimeline();
  toast('Split clip');
}

function deleteSelected() {
  if (clips.length === 0) return;
  const idx = clips.findIndex(c => c.id === selectedClipId);
  if (idx === -1) { toast('Select a clip first'); return; }

  clips.splice(idx, 1);

  if (clips.length > 0) {
    const newIdx = Math.min(idx, clips.length - 1);
    selectedClipId = clips[newIdx].id;
    activeClipIndex = newIdx;
    seekTimeline(getTimelineStartOfClip(newIdx));
  } else {
    selectedClipId = null;
    activeClipIndex = 0;
    timelineTime = 0;
    video.src = '';
  }

  renderTimeline();
  toast('Clip deleted');
}

// ── Clip Context Menu ──────────────────────────────────────────
const SPEED_OPTIONS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 4];
let ctxMenuEl = null;

function closeContextMenu() {
  if (ctxMenuEl) { ctxMenuEl.remove(); ctxMenuEl = null; }
}

function showClipContextMenu(e, clipId, clipEl) {
  e.preventDefault();
  e.stopPropagation();
  closeContextMenu();

  const clip = clips.find(c => c.id === clipId);
  if (!clip) return;
  selectClip(clipId);

  // Source time at the cursor (used as the split point).
  const r = clipEl.getBoundingClientRect();
  const frac = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
  const splitTime = clip.startTime + frac * (clip.endTime - clip.startTime);

  const menu = document.createElement('div');
  menu.className = 'context-menu';

  const split = document.createElement('div');
  split.className = 'ctx-item';
  split.innerHTML = `<span>✂</span><span>Split here</span><kbd>S</kbd>`;
  split.onclick = () => { closeContextMenu(); splitClipAt(clipId, splitTime); };
  menu.appendChild(split);

  const del = document.createElement('div');
  del.className = 'ctx-item danger';
  del.innerHTML = `<span>🗑</span><span>Delete</span><kbd>Del</kbd>`;
  del.onclick = () => {
    closeContextMenu();
    showConfirm(`Delete this clip ("${clip.name}") from the timeline?`, () => {
      selectedClipId = clipId;
      deleteSelected();
    });
  };
  menu.appendChild(del);

  menu.appendChild(Object.assign(document.createElement('div'), { className: 'ctx-sep' }));
  menu.appendChild(Object.assign(document.createElement('div'), { className: 'ctx-label', textContent: 'Speed' }));

  const speeds = document.createElement('div');
  speeds.className = 'ctx-speeds';
  SPEED_OPTIONS.forEach(spd => {
    const chip = document.createElement('div');
    chip.className = 'ctx-speed' + (clip.speed === spd ? ' active' : '');
    chip.textContent = spd + '×';
    chip.onclick = () => { selectClip(clipId); setSpeed(spd); closeContextMenu(); };
    speeds.appendChild(chip);
  });
  menu.appendChild(speeds);

  // Position within the viewport.
  document.body.appendChild(menu);
  const mw = menu.offsetWidth, mh = menu.offsetHeight;
  const x = Math.min(e.clientX, window.innerWidth - mw - 8);
  const y = Math.min(e.clientY, window.innerHeight - mh - 8);
  menu.style.left = Math.max(8, x) + 'px';
  menu.style.top = Math.max(8, y) + 'px';
  ctxMenuEl = menu;
}

// Dismiss the context menu on outside interaction.
document.addEventListener('mousedown', e => {
  if (ctxMenuEl && !ctxMenuEl.contains(e.target)) closeContextMenu();
});
window.addEventListener('blur', closeContextMenu);
window.addEventListener('resize', closeContextMenu);
document.addEventListener('DOMContentLoaded', () => {
  const tw = document.getElementById('trackWrapper');
  if (tw) tw.addEventListener('scroll', closeContextMenu);
});



// ── Export ──────────────────────────────────────────────────────
function showExportModal() {
  if (clips.length === 0) { toast('Add clips to the timeline first'); return; }
  document.getElementById('exportModal').classList.add('show');
}
function hideExportModal() {
  document.getElementById('exportModal').classList.remove('show');
  document.getElementById('progressWrap').classList.remove('show');
}

let exportPollTimer = null;

async function doExport() {
  const fmt = document.getElementById('exportFormat').value;
  const filename = document.getElementById('exportFilename').value || 'output';
  const btn = document.getElementById('exportBtn');
  const cancelBtn = document.querySelector('#exportModal .btn-ghost');
  const pw = document.getElementById('progressWrap');
  const pb = document.getElementById('progressBar');

  btn.disabled = true;
  btn.textContent = 'Exporting...';
  cancelBtn.style.display = 'none';
  pw.classList.add('show');
  pb.style.width = '0%';

  // Poll progress
  exportPollTimer = setInterval(async () => {
    try {
      const r = await fetch('/api/export/progress');
      const d = await r.json();
      if (d.progress !== undefined) {
        pb.style.width = Math.min(99, d.progress) + '%';
      }
    } catch(_) {}
  }, 300);

  const resolution = parseInt(document.getElementById('exportResolution').value);

  try {
    const resp = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clips: clips.map(c => ({
          src: c.src,
          startTime: c.startTime,
          endTime: c.endTime,
          speed: c.speed
        })),
        format: fmt,
        filename: filename,
        resolution: resolution
      })
    });
    clearInterval(exportPollTimer);
    const data = await resp.json();
    pb.style.width = '100%';

    if (data.success) {
      toast('Exported to: ' + data.path);
      setTimeout(hideExportModal, 1500);
    } else {
      toast('Export error: ' + data.error, true);
    }
  } catch(e) {
    clearInterval(exportPollTimer);
    toast('Export failed: ' + e.message, true);
  } finally {
    clearInterval(exportPollTimer);
    btn.disabled = false;
    btn.textContent = 'Export';
    cancelBtn.style.display = '';
  }
}

// ── Keyboard Shortcuts ─────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  switch (e.code) {
    case 'Escape': closeContextMenu(); break;
    case 'Space': e.preventDefault(); togglePlay(); break;
    case 'KeyS': splitAtPlayhead(); break;
    case 'Delete': case 'Backspace': deleteSelected(); break;
    case 'ArrowLeft':
      e.preventDefault();
      if (e.shiftKey) stepFrame(-10); else stepFrame(-1);
      break;
    case 'ArrowRight':
      e.preventDefault();
      if (e.shiftKey) stepFrame(10); else stepFrame(1);
      break;
  }
});

// ── Toast ──────────────────────────────────────────────────────
function toast(msg, isError) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.borderColor = isError ? 'rgba(255,60,60,.5)' : 'rgba(124,92,252,.3)';
  el.classList.add('show');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove('show'), 3000);
}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

# Shared mutable export progress (written by export thread, read by poll handler)
_export_progress = {"progress": 0}


# ---------------------------------------------------------------------------
# ffmpeg fast-path helpers
# ---------------------------------------------------------------------------

def _ffmpeg_bin() -> str | None:
    """Return the ffmpeg executable path if available, else None (cached)."""
    if not hasattr(_ffmpeg_bin, "_cached"):
        _ffmpeg_bin._cached = shutil.which("ffmpeg")
    return _ffmpeg_bin._cached


def _ffprobe_bin() -> str | None:
    if not hasattr(_ffprobe_bin, "_cached"):
        _ffprobe_bin._cached = shutil.which("ffprobe")
    return _ffprobe_bin._cached


def _has_audio(path: Path) -> bool:
    """Return True if the media file contains at least one audio stream."""
    probe = _ffprobe_bin()
    if not probe:
        return False
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
        return "audio" in out.stdout
    except (subprocess.SubprocessError, OSError):
        return False


def _atempo_factors(speed: float) -> list[float]:
    """Decompose a playback-rate change into atempo factors within [0.5, 2.0]."""
    factors: list[float] = []
    s = speed
    while s > 2.0 + 1e-9:
        factors.append(2.0)
        s /= 2.0
    while s < 0.5 - 1e-9:
        factors.append(0.5)
        s /= 0.5
    factors.append(round(s, 6))
    return factors


class _Handler(BaseHTTPRequestHandler):
    """Request handler for the clip editor."""

    video_path: Path | None = None
    upload_dir: Path | None = None
    export_dir: Path | None = None

    def log_message(self, format, *args):  # noqa: A002
        pass  # silence console spam

    # ── GET ────────────────────────────────────────────────────
    def do_GET(self):  # noqa: N802
        try:
            path = unquote(self.path)

            if path == "/" or path == "":
                self._serve_html()
            elif path == "/api/export/progress":
                self._json_response(_export_progress)
            elif path.startswith("/video/"):
                self._serve_file(path[7:])
            elif path.startswith("/uploads/"):
                fpath = Path(self.upload_dir) / path[9:]
                self._serve_static(fpath)
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    # ── POST ───────────────────────────────────────────────────
    def do_POST(self):  # noqa: N802
        try:
            path = unquote(self.path)

            if path == "/api/upload":
                self._handle_upload()
            elif path == "/api/export":
                self._handle_export()
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    # ── Serve HTML (inject initial video) ──────────────────────
    def _serve_html(self):
        html = _HTML
        if self.video_path and self.video_path.exists():
            # Inject the initial video path
            inject = f'<script>window.__INITIAL_VIDEO__ = "/video/{self.video_path.name}";</script>'
            html = html.replace("</head>", inject + "\n</head>")

        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── Serve video file with Range support ────────────────────
    def _serve_file(self, name: str):
        fpath = self.video_path
        if fpath is None or fpath.name != name:
            # Maybe it's an uploaded file
            fpath = Path(self.upload_dir) / name
        if not fpath or not fpath.exists():
            self.send_error(404)
            return
        self._serve_static(fpath)

    def _serve_static(self, fpath: Path):
        if not fpath.exists():
            self.send_error(404)
            return

        mime, _ = mimetypes.guess_type(str(fpath))
        mime = mime or "application/octet-stream"
        fsize = fpath.stat().st_size

        # Range request support for video seeking
        range_header = self.headers.get("Range")
        if range_header:
            start, end = self._parse_range(range_header, fsize)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{fsize}")
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Type", mime)
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(fpath, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(fsize))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            with open(fpath, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    @staticmethod
    def _parse_range(header: str, fsize: int) -> tuple[int, int]:
        """Parse Range: bytes=start-end header."""
        spec = header.replace("bytes=", "").strip()
        parts = spec.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else fsize - 1
        return max(0, start), min(end, fsize - 1)

    # ── Upload ─────────────────────────────────────────────────
    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._json_response({"error": "Expected multipart/form-data"}, 400)
            return

        # Parse boundary
        boundary = content_type.split("boundary=")[-1].encode()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Extract filename and data from multipart
        parts = body.split(b"--" + boundary)
        for part in parts:
            if b"filename=" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            header_section = part[:header_end].decode("utf-8", errors="replace")
            file_data = part[header_end + 4:]
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]

            # Extract filename
            fname = "uploaded_video.mp4"
            for line in header_section.split("\n"):
                line = line.strip()
                if line.lower().startswith("content-disposition:"):
                    for segment in line.split(";"):
                        segment = segment.strip()
                        if segment.startswith("filename="):
                            fname = segment.split("=", 1)[1].strip('"')

            save_path = Path(self.upload_dir) / fname
            save_path.write_bytes(file_data)

            # Update handler's video path only if it wasn't set (do not overwrite initial video)
            if not _Handler.video_path:
                _Handler.video_path = save_path

            self._json_response({"src": f"/uploads/{fname}"})
            return

        self._json_response({"error": "No file found in upload"}, 400)

    # ── Export ─────────────────────────────────────────────────
    def _handle_export(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            params = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "Invalid JSON"}, 400)
            return

        clip_defs = params.get("clips", [])
        fmt = params.get("format", "mp4")
        filename = params.get("filename", "output")
        resolution = params.get("resolution", 0)

        if not clip_defs:
            self._json_response({"error": "No clips"}, 400)
            return

        try:
            if _ffmpeg_bin():
                try:
                    out_path = self._ffmpeg_export(clip_defs, fmt, filename, resolution)
                except Exception:
                    # ffmpeg path failed — fall back to the pure-Python encoder
                    out_path = self._do_export(clip_defs, fmt, filename, resolution)
            else:
                out_path = self._do_export(clip_defs, fmt, filename, resolution)
            self._json_response({"success": True, "path": str(out_path)})
        except Exception as exc:
            self._json_response({"error": str(exc)}, 500)

    def _resolve_clip_src(self, src: str) -> Path:
        """Resolve frontend src URL path to local absolute file path."""
        if src.startswith("/video/"):
            if self.video_path and self.video_path.name == src[7:]:
                return self.video_path
            return Path(self.upload_dir) / src[7:]
        elif src.startswith("/uploads/"):
            return Path(self.upload_dir) / src[9:]
        else:
            name = src.split("/")[-1]
            return Path(self.upload_dir) / name

    def _ffmpeg_export(self, clip_defs: list[dict], fmt: str, filename: str, resolution: int = 0) -> Path:
        """Fast export via ffmpeg: trim + speed + scale + concat in one multithreaded pass.

        Far faster than the frame-by-frame cv2 path (hardware/SIMD encoders, no
        per-frame Python overhead) and preserves audio when every clip has it.
        """
        global _export_progress
        _export_progress["progress"] = 0

        ffmpeg = _ffmpeg_bin()
        if not ffmpeg:
            raise RuntimeError("ffmpeg not available")

        srcs = [self._resolve_clip_src(c["src"]) for c in clip_defs]

        # Target geometry / fps come from the first clip (concat needs uniform size).
        first = cv2.VideoCapture(str(srcs[0]))
        if not first.isOpened():
            raise RuntimeError(f"Cannot open video: {srcs[0]}")
        src_fps = first.get(cv2.CAP_PROP_FPS) or 30.0
        orig_w = int(first.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(first.get(cv2.CAP_PROP_FRAME_HEIGHT))
        first.release()
        if orig_w <= 0 or orig_h <= 0:
            raise RuntimeError("Could not determine source dimensions")

        if resolution > 0:
            w = resolution
            h = int(orig_h * (resolution / orig_w))
        else:
            w, h = orig_w, orig_h
        w += w % 2
        h += h % 2  # codecs require even dimensions

        is_gif = fmt == "gif"
        out_fps = min(src_fps, 15.0) if is_gif else src_fps
        # Audio only when every clip has it (concat needs matching stream sets).
        want_audio = (not is_gif) and all(_has_audio(s) for s in srcs)

        total_out_dur = 0.0
        for c in clip_defs:
            speed = float(c.get("speed", 1.0)) or 1.0
            total_out_dur += max(0.0, (c["endTime"] - c["startTime"]) / speed)
        total_out_dur = max(total_out_dur, 0.001)

        # ── Build filter graph ──────────────────────────────────────
        inputs: list[str] = []
        for s in srcs:
            inputs += ["-i", str(s)]

        parts: list[str] = []
        concat_labels: list[str] = []
        for i, c in enumerate(clip_defs):
            start = float(c["startTime"])
            end = float(c["endTime"])
            speed = float(c.get("speed", 1.0)) or 1.0
            vlbl = f"v{i}"
            parts.append(
                f"[{i}:v]trim=start={start}:end={end},setpts=(PTS-STARTPTS)/{speed},"
                f"fps={out_fps},scale={w}:{h}:flags=bicubic,setsar=1[{vlbl}]"
            )
            concat_labels.append(f"[{vlbl}]")
            if want_audio:
                albl = f"a{i}"
                atempo = ",".join(f"atempo={f}" for f in _atempo_factors(speed))
                parts.append(
                    f"[{i}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,{atempo}[{albl}]"
                )
                concat_labels.append(f"[{albl}]")

        n = len(clip_defs)
        if want_audio:
            parts.append("".join(concat_labels) + f"concat=n={n}:v=1:a=1[vc][outa]")
            vcat = "[vc]"
        else:
            parts.append("".join(concat_labels) + f"concat=n={n}:v=1:a=0[vc]")
            vcat = "[vc]"

        if is_gif:
            parts.append(
                f"{vcat}split[s0][s1];[s0]palettegen=stats_mode=diff[p];"
                f"[s1][p]paletteuse=dither=bayer[outv]"
            )
            vmap = "[outv]"
        else:
            vmap = vcat

        filtergraph = ";".join(parts)

        out_path = Path(self.export_dir) / f"{filename}.{fmt}"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [ffmpeg, "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
               *inputs, "-filter_complex", filtergraph, "-map", vmap]
        if want_audio:
            cmd += ["-map", "[outa]"]

        if not is_gif:
            codec = {"mp4": "libx264", "avi": "mpeg4", "webm": "libvpx"}.get(fmt, "libx264")
            cmd += ["-c:v", codec]
            if codec == "libx264":
                cmd += ["-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart"]
            elif codec == "libvpx":
                cmd += ["-b:v", "2M", "-deadline", "realtime", "-cpu-used", "5"]
            else:  # mpeg4 / avi
                cmd += ["-qscale:v", "4"]
            if want_audio:
                acodec = {"webm": "libvorbis"}.get(fmt, "aac")
                cmd += ["-c:a", acodec]
        cmd.append(str(out_path))

        self._run_ffmpeg(cmd, total_out_dur)
        _export_progress["progress"] = 100
        return out_path

    def _run_ffmpeg(self, cmd: list[str], total_dur: float) -> None:
        """Run ffmpeg, streaming -progress output into _export_progress."""
        global _export_progress
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", bufsize=1,
        )
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                    try:
                        secs = int(line.split("=", 1)[1]) / 1_000_000.0
                        _export_progress["progress"] = min(99, int(secs / total_dur * 100))
                    except ValueError:
                        pass
        finally:
            proc.stdout.close()
            stderr = proc.stderr.read()
            proc.stderr.close()
            ret = proc.wait()
        if ret != 0:
            raise RuntimeError(f"ffmpeg failed: {stderr.strip()[-500:]}")

    def _do_export(self, clip_defs: list[dict], fmt: str, filename: str, resolution: int = 0) -> Path:
        """Run the actual export using cv2."""
        global _export_progress
        _export_progress["progress"] = 0

        if not clip_defs:
            raise ValueError("No clips to export")

        first_clip_src = self._resolve_clip_src(clip_defs[0]["src"])
        first_cap = cv2.VideoCapture(str(first_clip_src))
        if not first_cap.isOpened():
            raise RuntimeError(f"Cannot open video: {first_clip_src}")

        fps = first_cap.get(cv2.CAP_PROP_FPS) or 30.0
        orig_w = int(first_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(first_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        first_cap.release()

        # Apply resolution scaling
        if resolution > 0 and orig_w > 0:
            scale = resolution / orig_w
            w = resolution
            h = int(orig_h * scale)
            # Ensure even dimensions for video codecs
            h = h + (h % 2)
        else:
            w = orig_w
            h = orig_h

        # Pre-calculate total frames for progress tracking
        total_frames = 0
        for clip in clip_defs:
            clip_src = self._resolve_clip_src(clip["src"])
            cap = cv2.VideoCapture(str(clip_src))
            if cap.isOpened():
                clip_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                start_f = int(clip["startTime"] * clip_fps)
                end_f = int(clip["endTime"] * clip_fps)
                speed = clip.get("speed", 1.0)
                if fmt == "gif":
                    sample_every = max(1, int(round(speed)))
                    total_frames += (end_f - start_f + 1) // sample_every
                else:
                    step = max(1.0, speed)
                    total_frames += int((end_f - start_f) / step) + 1
                cap.release()
        total_frames = max(1, total_frames)
        processed_frames = 0

        ext = fmt if fmt != "gif" else "gif"
        out_path = Path(self.export_dir) / f"{filename}.{ext}"

        if fmt == "gif":
            frames_for_gif: list[Image.Image] = []
            for clip in clip_defs:
                clip_src = self._resolve_clip_src(clip["src"])
                cap = cv2.VideoCapture(str(clip_src))
                if not cap.isOpened():
                    raise RuntimeError(f"Cannot open clip source: {clip_src}")

                clip_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                start_f = int(clip["startTime"] * clip_fps)
                end_f = int(clip["endTime"] * clip_fps)
                speed = clip.get("speed", 1.0)
                sample_every = max(1, int(round(speed)))

                cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
                fi = start_f
                while fi <= end_f:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if (fi - start_f) % sample_every == 0:
                        if frame.shape[1] != w or frame.shape[0] != h:
                            frame = cv2.resize(frame, (w, h))
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        frames_for_gif.append(Image.fromarray(frame_rgb))
                        processed_frames += 1
                        # GIF: frame extraction is ~70% of the work, saving is ~30%
                        _export_progress["progress"] = int(processed_frames / total_frames * 70)
                    fi += 1
                cap.release()

            if not frames_for_gif:
                raise ValueError("No frames extracted for GIF")

            _export_progress["progress"] = 75
            gif_fps = fps / max(1, int(round(clip_defs[0].get("speed", 1.0))))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            frames_for_gif[0].save(
                out_path, save_all=True, append_images=frames_for_gif[1:],
                duration=int(1000 / gif_fps), loop=0, optimize=True,
            )
            for fr in frames_for_gif:
                fr.close()
        else:
            fourcc_map = {
                "mp4": "mp4v",
                "avi": "XVID",
                "webm": "VP80",
            }
            fourcc = cv2.VideoWriter_fourcc(*fourcc_map.get(fmt, "mp4v"))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
            if not writer.isOpened():
                raise RuntimeError(f"Cannot create video writer for format: {fmt}")

            try:
                for clip in clip_defs:
                    clip_src = self._resolve_clip_src(clip["src"])
                    cap = cv2.VideoCapture(str(clip_src))
                    if not cap.isOpened():
                        raise RuntimeError(f"Cannot open clip source: {clip_src}")

                    clip_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                    start_f = int(clip["startTime"] * clip_fps)
                    end_f = int(clip["endTime"] * clip_fps)
                    speed = clip.get("speed", 1.0)
                    step = max(1.0, speed)

                    # Read sequentially (decoders are optimized for forward reads);
                    # seeking per-frame with cap.set() is orders of magnitude slower.
                    cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
                    fi = start_f
                    next_write = float(start_f)
                    while fi <= end_f:
                        ok, frame = cap.read()
                        if not ok:
                            break
                        if fi >= next_write - 1e-9:
                            if frame.shape[1] != w or frame.shape[0] != h:
                                frame = cv2.resize(frame, (w, h))
                            writer.write(frame)
                            processed_frames += 1
                            _export_progress["progress"] = int(processed_frames / total_frames * 95)
                            next_write += step
                        fi += 1
                    cap.release()
            finally:
                writer.release()

        _export_progress["progress"] = 100
        return out_path

    # ── Helpers ────────────────────────────────────────────────
    def _json_response(self, data: dict, code: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads so progress polling works during export."""
    daemon_threads = True


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def launch_gui(video_path: Path | None = None) -> None:
    """Launch the ClipVideo web editor."""
    port = _find_free_port()
    upload_dir = Path(tempfile.mkdtemp(prefix="clipvideo_uploads_"))
    export_dir = Path.cwd()

    # Resolve to absolute so relative paths work for file serving
    if video_path is not None:
        video_path = video_path.resolve()

    _Handler.video_path = video_path
    _Handler.upload_dir = upload_dir
    _Handler.export_dir = export_dir

    server = _ThreadedHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}"

    print(f"ClipVideo Editor running at {url}")
    print("Press Ctrl+C to stop.")

    # Open browser
    threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
