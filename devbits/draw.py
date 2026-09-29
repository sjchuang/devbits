"""Web-based, Windows Paint-style image editor for ``devbits draw``.

Launches a local HTTP server and opens the browser. All drawing happens
client-side on a ``<canvas>``; the backend only decodes the initial image
(or formats the browser cannot read) and writes saved files to disk via Pillow.
"""

from __future__ import annotations

import io
import json
import os
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps

# Extensions Save accepts, mapped to the Pillow format used to write them.
_SAVE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".bmp": "BMP",
    ".gif": "GIF",
    ".webp": "WEBP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".ico": "ICO",
    ".pgm": "PPM",  # Pillow's PPM writer emits P5 (PGM) for grayscale images
    ".ppm": "PPM",
}

_MAX_BODY = 512 * 1024 * 1024  # refuse uploads larger than 512 MB

# ---------------------------------------------------------------------------
# HTML / CSS / JS – embedded as a single-page app
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Devbits.Draw</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%237c5cfc'/%3E%3Cstop offset='1' stop-color='%2300d4ff'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='64' height='64' rx='15' fill='url(%23g)'/%3E%3Cpath d='M42 14l8 8-18 18-9 2 2-9z' fill='%23fff'/%3E%3Cpath d='M20 44c-4 0-6 3-6 6h8c2 0 3-2 3-3z' fill='%23fff'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{font-size:13px}
body{
  font-family:'Inter',system-ui,sans-serif;background:#09090e;color:#e0e0f0;
  height:100vh;overflow:hidden;display:flex;flex-direction:column;user-select:none;
}
svg.i{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}

/* ── Top bar ─────────────────────────────────────────────── */
.topbar{
  display:flex;align-items:center;gap:6px;padding:0 14px;height:46px;min-height:46px;
  background:#0f0f18;border-bottom:1px solid rgba(255,255,255,.06);
}
.logo{
  font-weight:700;font-size:1.1rem;margin-right:10px;
  background:linear-gradient(135deg,#7c5cfc,#00d4ff);-webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.filename{color:#8888aa;font-size:.9rem;margin-right:auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sep{width:1px;height:22px;background:rgba(255,255,255,.08);margin:0 4px}
.btn{
  padding:6px 12px;border-radius:7px;border:none;cursor:pointer;font-family:inherit;font-weight:600;font-size:.88rem;
  display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.06);color:#c0c0da;transition:background .12s;
  white-space:nowrap;
}
.btn:hover{background:rgba(255,255,255,.12)}
.btn:disabled{opacity:.4;pointer-events:none}
.btn.icon{padding:6px}
.btn-primary{background:linear-gradient(135deg,#7c5cfc,#5c3cd6);color:#fff}
.btn-primary:hover{background:linear-gradient(135deg,#8d6eff,#6d4de6)}

/* ── Ribbon ──────────────────────────────────────────────── */
.ribbon{
  display:flex;align-items:stretch;gap:0;padding:6px 8px 4px;background:#0d0d14;
  border-bottom:1px solid rgba(255,255,255,.06);overflow-x:auto;overflow-y:hidden;min-height:92px;
}
.group{display:flex;flex-direction:column;align-items:center;padding:0 10px;border-right:1px solid rgba(255,255,255,.06)}
.group:last-child{border-right:none}
.group .body{flex:1;display:flex;align-items:center;gap:4px}
.group .label{font-size:.72rem;color:#666688;margin-top:3px;letter-spacing:.3px}
.tb{
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;min-width:38px;
  padding:4px 6px;border-radius:6px;border:1px solid transparent;background:none;color:#c0c0da;cursor:pointer;
  font-family:inherit;font-size:.74rem;
}
.tb:hover{background:rgba(255,255,255,.07)}
.tb.active{background:rgba(124,92,252,.22);border-color:rgba(124,92,252,.6);color:#fff}
.tb.big svg.i{width:26px;height:26px}
.tb.sm{flex-direction:row;padding:3px 6px;min-width:0;gap:5px;font-size:.78rem}
.col{display:flex;flex-direction:column;gap:1px}
.grid3{display:grid;grid-template-columns:repeat(3,30px);gap:2px}
.grid3 .tb{min-width:0;padding:5px}
.shapes{
  display:grid;grid-template-columns:repeat(7,24px);grid-auto-rows:24px;gap:2px;max-height:78px;overflow-y:auto;
  padding:2px;background:rgba(255,255,255,.03);border-radius:6px;
}
.shapes .tb{min-width:0;padding:0}
.shapes canvas{width:20px;height:20px}
.caret{font-size:.6rem;opacity:.7}

/* colors */
.cslot{display:flex;flex-direction:column;align-items:center;gap:3px;padding:3px 4px;border-radius:6px;border:1px solid transparent;cursor:pointer;font-size:.72rem;color:#aaaacc;background:none;font-family:inherit}
.cslot.active{background:rgba(124,92,252,.22);border-color:rgba(124,92,252,.6);color:#fff}
.cslot .sw{width:30px;height:30px;border-radius:5px;border:2px solid rgba(255,255,255,.3)}
.cslot:nth-of-type(2) .sw{width:24px;height:24px;margin:3px}
.palette{display:grid;grid-template-columns:repeat(10,18px);grid-auto-rows:18px;gap:3px}
.palette .p{border-radius:3px;border:1px solid rgba(255,255,255,.18);cursor:pointer}
.palette .p:hover{transform:scale(1.15);border-color:#fff}
.palette .p.empty{background:transparent;border-style:dashed;cursor:default}
.palette .p.empty:hover{transform:none;border-color:rgba(255,255,255,.18)}

/* ── Options bar ─────────────────────────────────────────── */
.optbar{
  display:flex;align-items:center;gap:12px;padding:0 14px;height:38px;min-height:38px;background:#0b0b12;
  border-bottom:1px solid rgba(255,255,255,.06);font-size:.85rem;color:#aaaacc;white-space:nowrap;overflow-x:auto;
}
.optbar .tname{font-weight:700;color:#e0e0f0;min-width:84px}
.optbar label{display:inline-flex;align-items:center;gap:6px;cursor:pointer}
.optbar .hint{color:#666688;font-size:.8rem}
.opt{display:none;align-items:center;gap:12px}
.opt.show{display:inline-flex}
input[type=range]{accent-color:#7c5cfc;width:120px}
input[type=number],input[type=text],select{
  background:#15151f;color:#e0e0f0;border:1px solid rgba(255,255,255,.12);border-radius:6px;padding:4px 7px;
  font-family:inherit;font-size:.85rem;
}
input[type=number]{width:64px}
input[type=checkbox],input[type=radio]{accent-color:#7c5cfc}
.seg{display:inline-flex;background:#15151f;border:1px solid rgba(255,255,255,.12);border-radius:6px;overflow:hidden}
.seg button{background:none;border:none;color:#aaaacc;padding:4px 10px;cursor:pointer;font-family:inherit;font-size:.82rem;font-weight:600}
.seg button+button{border-left:1px solid rgba(255,255,255,.08)}
.seg button.on{background:rgba(124,92,252,.3);color:#fff}

/* ── Workspace ───────────────────────────────────────────── */
.workspace{flex:1;overflow:auto;background:#16161f;position:relative;min-height:0}
.workspace.drop{outline:2px dashed #7c5cfc;outline-offset:-8px}
.wrap{display:grid;place-items:center;min-width:100%;min-height:100%;width:max-content;height:max-content;padding:40px 56px 56px 40px}
.stage{
  position:relative;box-shadow:0 4px 30px rgba(0,0,0,.5);
  background-color:#fff;
  background-image:linear-gradient(45deg,#ccc 25%,transparent 25%),linear-gradient(-45deg,#ccc 25%,transparent 25%),
    linear-gradient(45deg,transparent 75%,#ccc 75%),linear-gradient(-45deg,transparent 75%,#ccc 75%);
  background-size:16px 16px;background-position:0 0,0 8px,8px -8px,-8px 0;
}
.stage canvas{position:absolute;left:0;top:0;display:block}
.stage.px canvas{image-rendering:pixelated}
#grid{position:absolute;inset:0;pointer-events:none;display:none}
.box{position:absolute;outline:1px dashed #3b82f6;box-shadow:0 0 0 1px rgba(255,255,255,.6);cursor:move;display:none}
.box .h{position:absolute;width:9px;height:9px;background:#fff;border:1px solid #3b82f6;border-radius:2px}
.h[data-h=nw]{left:-5px;top:-5px;cursor:nwse-resize}.h[data-h=n]{left:calc(50% - 5px);top:-5px;cursor:ns-resize}
.h[data-h=ne]{right:-5px;top:-5px;cursor:nesw-resize}.h[data-h=e]{right:-5px;top:calc(50% - 5px);cursor:ew-resize}
.h[data-h=se]{right:-5px;bottom:-5px;cursor:nwse-resize}.h[data-h=s]{left:calc(50% - 5px);bottom:-5px;cursor:ns-resize}
.h[data-h=sw]{left:-5px;bottom:-5px;cursor:nesw-resize}.h[data-h=w]{left:-5px;top:calc(50% - 5px);cursor:ew-resize}
.cvh{position:absolute;width:9px;height:9px;background:#e0e0f0;border:1px solid #555;border-radius:2px}
.cvh[data-cv=r]{cursor:ew-resize}.cvh[data-cv=b]{cursor:ns-resize}.cvh[data-cv=br]{cursor:nwse-resize}
#ghost{position:absolute;left:0;top:0;border:1px dashed #7c5cfc;pointer-events:none;display:none}
#ring{position:absolute;pointer-events:none;border:1px solid #fff;mix-blend-mode:difference;display:none}
.text-box{position:absolute;border:1px dashed #3b82f6;padding:4px;cursor:move;background:rgba(59,130,246,.04)}
.text-box textarea{
  display:block;border:none;outline:none;resize:both;overflow:hidden;padding:0;margin:0;background:transparent;
  line-height:1.2;white-space:pre-wrap;overflow-wrap:break-word;cursor:text;min-width:20px;min-height:1em;
}

/* ── Status bar ──────────────────────────────────────────── */
.status{
  display:flex;align-items:center;gap:22px;padding:0 14px;height:30px;min-height:30px;background:#0f0f18;
  border-top:1px solid rgba(255,255,255,.06);font-size:.8rem;color:#8888aa;font-variant-numeric:tabular-nums;
}
.status .zoom{margin-left:auto;display:flex;align-items:center;gap:8px}
.status .zoom button{background:none;border:none;color:#aaaacc;cursor:pointer;font-size:1rem;width:20px}
.status .zoom span{min-width:46px;text-align:right}

/* ── Menus / dialogs / toast ─────────────────────────────── */
.menu{
  position:fixed;z-index:500;display:none;min-width:190px;background:#161622;border:1px solid rgba(255,255,255,.1);
  border-radius:8px;padding:4px;box-shadow:0 10px 40px rgba(0,0,0,.6);
}
.menu.show{display:block}
.menu button{
  display:flex;align-items:center;gap:9px;width:100%;background:none;border:none;color:#d0d0e8;text-align:left;
  padding:7px 10px;border-radius:5px;cursor:pointer;font-family:inherit;font-size:.86rem;
}
.menu button:hover{background:rgba(124,92,252,.22)}
.menu button.on::after{content:'✓';margin-left:auto;color:#a58cff}
.menu hr{border:none;border-top:1px solid rgba(255,255,255,.08);margin:4px 2px}
.menu .kbd{margin-left:auto;color:#666688;font-size:.76rem}
dialog{
  margin:auto;background:#12121c;color:#e0e0f0;border:1px solid rgba(255,255,255,.1);border-radius:12px;
  padding:20px 22px;min-width:340px;box-shadow:0 20px 60px rgba(0,0,0,.7);
}
dialog::backdrop{background:rgba(0,0,0,.55)}
dialog h2{font-size:1.1rem;margin-bottom:14px}
dialog .row{display:flex;align-items:center;gap:10px;margin:9px 0}
dialog .row>span:first-child{width:118px;color:#aaaacc}
dialog fieldset{border:1px solid rgba(255,255,255,.1);border-radius:8px;padding:8px 12px;margin:10px 0}
dialog legend{padding:0 6px;color:#8888aa;font-size:.8rem}
dialog .actions{display:flex;flex-direction:row-reverse;justify-content:flex-start;gap:8px;margin-top:18px}
dialog .note{color:#777799;font-size:.8rem;word-break:break-all}
.toast{
  position:fixed;right:18px;bottom:44px;z-index:600;background:#1c1c2a;border:1px solid rgba(255,255,255,.1);
  border-radius:8px;padding:10px 14px;font-size:.86rem;max-width:520px;box-shadow:0 8px 30px rgba(0,0,0,.5);
  opacity:0;transform:translateY(8px);transition:all .2s;pointer-events:none;word-break:break-all;
}
.toast.show{opacity:1;transform:none}
.toast.err{border-color:rgba(255,80,80,.5);color:#ff9b9b}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:5px}
::-webkit-scrollbar-track{background:transparent}
</style>
</head>
<body>

<div class="topbar">
  <span class="logo">Devbits.Draw</span>
  <span class="filename" id="fname">untitled.png</span>
  <button class="btn" data-act="new" title="New canvas"><svg class="i" data-icon="new"></svg>New</button>
  <button class="btn" data-act="open" title="Open an image (Ctrl+O)"><svg class="i" data-icon="open"></svg>Open</button>
  <button class="btn" data-act="insert" title="Add a photo onto the canvas as a movable selection"><svg class="i" data-icon="image"></svg>Add photo</button>
  <span class="sep"></span>
  <button class="btn icon" data-act="undo" id="btnUndo" title="Undo (Ctrl+Z)"><svg class="i" data-icon="undo"></svg></button>
  <button class="btn icon" data-act="redo" id="btnRedo" title="Redo (Ctrl+Y)"><svg class="i" data-icon="redo"></svg></button>
  <span class="sep"></span>
  <button class="btn" data-act="download" title="Download through the browser"><svg class="i" data-icon="download"></svg>Download</button>
  <button class="btn" data-act="saveas" title="Save as (Ctrl+Shift+S)">Save as</button>
  <button class="btn btn-primary" data-act="save" title="Save (Ctrl+S)"><svg class="i" data-icon="save"></svg>Save</button>
</div>

<div class="ribbon">
  <div class="group">
    <div class="body">
      <button class="tb big" data-act="paste" title="Paste (Ctrl+V)"><svg class="i" data-icon="paste"></svg>Paste</button>
      <div class="col">
        <button class="tb sm" data-act="cut" title="Cut (Ctrl+X)"><svg class="i" data-icon="cut"></svg>Cut</button>
        <button class="tb sm" data-act="copy" title="Copy (Ctrl+C)"><svg class="i" data-icon="copy"></svg>Copy</button>
      </div>
    </div>
    <div class="label">Clipboard</div>
  </div>

  <div class="group">
    <div class="body">
      <button class="tb big" data-tool="select" data-menu="mSelect" title="Select (S)"><svg class="i" data-icon="select" id="selIcon"></svg><span>Select <span class="caret">▼</span></span></button>
      <div class="col">
        <button class="tb sm" data-act="crop" title="Crop to selection (Ctrl+Shift+X)"><svg class="i" data-icon="crop"></svg>Crop</button>
        <button class="tb sm" data-act="resize" title="Resize and skew"><svg class="i" data-icon="resize"></svg>Resize</button>
        <button class="tb sm" data-menu="mRotate" title="Rotate or flip"><svg class="i" data-icon="rotate"></svg>Rotate <span class="caret">▼</span></button>
      </div>
    </div>
    <div class="label">Image</div>
  </div>

  <div class="group">
    <div class="body">
      <div class="grid3">
        <button class="tb" data-tool="pencil" title="Pencil (P)"><svg class="i" data-icon="pencil"></svg></button>
        <button class="tb" data-tool="fill" title="Fill with color (F)"><svg class="i" data-icon="fill"></svg></button>
        <button class="tb" data-tool="text" title="Text (T)"><svg class="i" data-icon="text"></svg></button>
        <button class="tb" data-tool="eraser" title="Eraser (E)"><svg class="i" data-icon="eraser"></svg></button>
        <button class="tb" data-tool="picker" title="Color picker (I)"><svg class="i" data-icon="picker"></svg></button>
        <button class="tb" data-tool="zoom" title="Magnifier (Z)"><svg class="i" data-icon="zoomin"></svg></button>
      </div>
    </div>
    <div class="label">Tools</div>
  </div>

  <div class="group">
    <div class="body">
      <button class="tb big" data-tool="brush" data-menu="mBrush" title="Brushes (B)"><svg class="i" data-icon="brush"></svg><span>Brushes <span class="caret">▼</span></span></button>
    </div>
    <div class="label">Brushes</div>
  </div>

  <div class="group">
    <div class="body">
      <div class="shapes" id="shapes"></div>
      <div class="col">
        <button class="tb sm" data-menu="mOutline" title="Shape outline"><svg class="i" data-icon="outline"></svg>Outline <span class="caret">▼</span></button>
        <button class="tb sm" data-menu="mFill" title="Shape fill (uses Color 2)"><svg class="i" data-icon="shapefill"></svg>Fill <span class="caret">▼</span></button>
      </div>
    </div>
    <div class="label">Shapes</div>
  </div>

  <div class="group">
    <div class="body">
      <button class="cslot active" id="slot1" title="Color 1 (foreground, left click)"><span class="sw" id="sw1"></span>Color 1</button>
      <button class="cslot" id="slot2" title="Color 2 (background, right click)"><span class="sw" id="sw2"></span>Color 2</button>
      <button class="tb" data-act="swap" title="Swap colors (X)"><svg class="i" data-icon="swap"></svg></button>
      <div class="palette" id="palette"></div>
      <button class="tb" data-act="editcolor" title="Edit colors"><svg class="i" data-icon="palette"></svg>Edit<br>colors</button>
      <input type="color" id="colorInput" style="position:absolute;opacity:0;pointer-events:none;width:0;height:0">
    </div>
    <div class="label">Colors</div>
  </div>

  <div class="group">
    <div class="body">
      <div class="col">
        <button class="tb sm" data-act="zoomin" title="Zoom in (Ctrl +)"><svg class="i" data-icon="zoomin"></svg>Zoom in</button>
        <button class="tb sm" data-act="zoomout" title="Zoom out (Ctrl -)"><svg class="i" data-icon="zoomout"></svg>Zoom out</button>
      </div>
      <div class="col">
        <button class="tb sm" data-act="zoom100" title="Actual size (Ctrl+1)"><svg class="i" data-icon="one"></svg>100%</button>
        <button class="tb sm" data-act="fit" title="Fit to window (Ctrl+0)"><svg class="i" data-icon="fit"></svg>Fit</button>
      </div>
      <div class="col">
        <button class="tb sm" data-act="grid" id="btnGrid" title="Pixel gridlines, shown at 400% and above (Ctrl+G)"><svg class="i" data-icon="grid"></svg>Gridlines</button>
        <button class="tb sm" data-act="props" title="Canvas size (Ctrl+E)"><svg class="i" data-icon="canvas"></svg>Canvas</button>
      </div>
    </div>
    <div class="label">View</div>
  </div>
</div>

<div class="optbar">
  <span class="tname" id="toolName">Pencil</span>
  <span class="opt" id="optSize"><label>Size <input type="range" id="size" min="1" max="100" value="1"> <input type="number" id="sizeNum" min="1" max="500" value="1"> px</label></span>
  <span class="opt" data-for="select">
    <span class="seg" id="selMode"><button data-v="rect" class="on">Rectangular</button><button data-v="free">Free-form</button></span>
    <label title="Pixels matching Color 2 become see-through while moving the selection"><input type="checkbox" id="transSel"> Transparent selection</label>
    <button class="btn" data-act="selectall">Select all</button>
    <button class="btn" data-act="crop">Crop</button>
    <button class="btn" data-act="delete">Delete</button>
    <span class="hint">Ctrl+drag duplicates · arrows nudge</span>
  </span>
  <span class="opt" data-for="brush">
    <label>Style <select id="brushSel"></select></label>
    <span class="hint">Right-drag paints with Color 2</span>
  </span>
  <span class="opt" data-for="pencil"><span class="hint">Left: Color 1 · Right: Color 2 · Shift: straight line</span></span>
  <span class="opt" data-for="eraser">
    <label><input type="checkbox" id="eraseTrans"> Erase to transparent</label>
    <span class="hint">Right-drag replaces only Color 1 with Color 2</span>
  </span>
  <span class="opt" data-for="fill">
    <label>Tolerance <input type="range" id="tol" min="0" max="100" value="10"> <span id="tolVal">10%</span></label>
    <span class="hint">Left: Color 1 · Right: Color 2</span>
  </span>
  <span class="opt" data-for="text">
    <select id="fontFam"></select>
    <input type="number" id="fontSize" min="4" max="999" value="28" title="Font size (px)">
    <span class="seg" id="fontStyle"><button data-v="bold"><b>B</b></button><button data-v="italic"><i>I</i></button><button data-v="underline"><u>U</u></button><button data-v="strike"><s>S</s></button></span>
    <label title="Fill the text box with Color 2"><input type="checkbox" id="textOpaque"> Opaque background</label>
    <span class="hint">Click or drag to place a box · click outside to apply</span>
  </span>
  <span class="opt" data-for="shape">
    <span class="hint" id="shapeHint"></span>
  </span>
  <span class="opt" data-for="picker"><span class="hint">Left click sets Color 1 · right click sets Color 2</span></span>
  <span class="opt" data-for="zoom"><span class="hint">Left click zooms in · right click zooms out · Ctrl+wheel anywhere</span></span>
</div>

<div class="workspace" id="ws">
  <div class="wrap">
    <div class="stage" id="stage">
      <canvas id="doc"></canvas>
      <canvas id="preview"></canvas>
      <div id="grid"></div>
      <div class="box" id="selbox"></div>
      <div class="box" id="shapebox"></div>
      <div id="ring"></div>
      <div id="ghost"></div>
      <div class="cvh" data-cv="r"></div>
      <div class="cvh" data-cv="b"></div>
      <div class="cvh" data-cv="br"></div>
    </div>
  </div>
</div>

<div class="status">
  <span id="stPos">&nbsp;</span>
  <span id="stSel"></span>
  <span id="stSize"></span>
  <div class="zoom">
    <button data-act="zoomout" title="Zoom out">−</button>
    <input type="range" id="zoomSlider" min="-400" max="500" value="0" style="width:140px">
    <button data-act="zoomin" title="Zoom in">+</button>
    <span id="stZoom">100%</span>
  </div>
</div>

<!-- menus -->
<div class="menu" id="mSelect">
  <button data-selmode="rect"><svg class="i" data-icon="select"></svg>Rectangular selection</button>
  <button data-selmode="free"><svg class="i" data-icon="lasso"></svg>Free-form selection</button>
  <hr>
  <button data-act="selectall"><svg class="i" data-icon="selall"></svg>Select all<span class="kbd">Ctrl+A</span></button>
  <button data-act="delete"><svg class="i" data-icon="trash"></svg>Delete selection<span class="kbd">Del</span></button>
  <button data-act="transsel" id="miTrans"><svg class="i" data-icon="trans"></svg>Transparent selection</button>
</div>
<div class="menu" id="mRotate">
  <button data-act="rotR"><svg class="i" data-icon="rotate"></svg>Rotate right 90°</button>
  <button data-act="rotL"><svg class="i" data-icon="rotateL"></svg>Rotate left 90°</button>
  <button data-act="rot180"><svg class="i" data-icon="rotate"></svg>Rotate 180°</button>
  <hr>
  <button data-act="flipV"><svg class="i" data-icon="flipV"></svg>Flip vertical</button>
  <button data-act="flipH"><svg class="i" data-icon="flipH"></svg>Flip horizontal</button>
  <hr>
  <button data-act="invert"><svg class="i" data-icon="invert"></svg>Invert colors</button>
</div>
<div class="menu" id="mBrush"></div>
<div class="menu" id="mOutline">
  <button data-outline="none">No outline</button>
  <button data-outline="solid">Solid</button>
  <button data-outline="dashed">Dashed</button>
  <button data-outline="dotted">Dotted</button>
</div>
<div class="menu" id="mFill">
  <button data-fill="none">No fill</button>
  <button data-fill="solid">Solid (Color 2)</button>
  <button data-fill="c1">Solid (Color 1)</button>
</div>

<!-- dialogs -->
<dialog id="dlgNew">
  <form method="dialog">
    <h2>New canvas</h2>
    <div class="row"><span>Width</span><input type="number" name="w" min="1" max="20000" required> px</div>
    <div class="row"><span>Height</span><input type="number" name="h" min="1" max="20000" required> px</div>
    <div class="row"><span>Background</span>
      <select name="bg"><option value="white">White</option><option value="c2">Color 2</option><option value="none">Transparent</option></select>
    </div>
    <div class="actions"><button class="btn btn-primary" value="ok">Create</button><button class="btn" value="cancel" formnovalidate>Cancel</button></div>
  </form>
</dialog>
<dialog id="dlgProps">
  <form method="dialog">
    <h2>Canvas size</h2>
    <div class="row"><span>Width</span><input type="number" name="w" min="1" max="20000" required> px</div>
    <div class="row"><span>Height</span><input type="number" name="h" min="1" max="20000" required> px</div>
    <p class="note">Crops or extends the canvas from the top-left corner. New area is filled with Color 2. To scale the picture, use Resize.</p>
    <div class="actions"><button class="btn btn-primary" value="ok">OK</button><button class="btn" value="cancel" formnovalidate>Cancel</button></div>
  </form>
</dialog>
<dialog id="dlgResize">
  <form method="dialog">
    <h2>Resize and skew <span class="note" id="rsTarget"></span></h2>
    <fieldset><legend>Resize</legend>
      <div class="row"><span>By</span>
        <label><input type="radio" name="by" value="pct" checked> Percentage</label>
        <label><input type="radio" name="by" value="px"> Pixels</label>
      </div>
      <div class="row"><span>Horizontal</span><input type="number" name="rw" min="1" step="any"></div>
      <div class="row"><span>Vertical</span><input type="number" name="rh" min="1" step="any"></div>
      <div class="row"><span></span><label><input type="checkbox" name="keep" checked> Maintain aspect ratio</label></div>
    </fieldset>
    <fieldset><legend>Skew (degrees)</legend>
      <div class="row"><span>Horizontal</span><input type="number" name="sx" min="-89" max="89" value="0" step="any"></div>
      <div class="row"><span>Vertical</span><input type="number" name="sy" min="-89" max="89" value="0" step="any"></div>
    </fieldset>
    <div class="actions"><button class="btn btn-primary" value="ok">OK</button><button class="btn" value="cancel" formnovalidate>Cancel</button></div>
  </form>
</dialog>
<dialog id="dlgSave">
  <form method="dialog">
    <h2>Save as</h2>
    <div class="row"><span>File name</span><input type="text" name="stem" style="width:220px" required></div>
    <div class="row"><span>Format</span>
      <select name="ext">
        <option value=".png">PNG (.png)</option><option value=".jpg">JPEG (.jpg)</option><option value=".bmp">Bitmap (.bmp)</option>
        <option value=".gif">GIF (.gif)</option><option value=".webp">WebP (.webp)</option><option value=".tiff">TIFF (.tiff)</option>
        <option value=".ico">Icon (.ico)</option>
        <option value=".pgm">Grayscale PGM (.pgm)</option><option value=".ppm">PPM (.ppm)</option>
      </select>
    </div>
    <p class="note">Folder: <span id="saveDir"></span></p>
    <p class="note">JPEG, BMP, PGM and PPM have no transparency — see-through pixels become white. PGM is saved as 8-bit grayscale.</p>
    <div class="actions"><button class="btn btn-primary" value="ok">Save</button><button class="btn" value="cancel" formnovalidate>Cancel</button></div>
  </form>
</dialog>

<input type="file" id="fileOpen" accept="image/*,.tif,.tiff,.ico,.pgm,.ppm,.pbm,.pnm" style="display:none">
<input type="file" id="fileInsert" accept="image/*,.tif,.tiff,.ico,.pgm,.ppm,.pbm,.pnm" style="display:none">
<div class="toast" id="toast"></div>

<script>
'use strict';
const CFG = window.__DRAW__ || {};
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// ── Icons ────────────────────────────────────────────────────
const ICONS = {
  new:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M12 12v6M9 15h6"/>',
  open:'<path d="M3 19V6a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v2"/><path d="M3 19l2.6-7.2A2 2 0 0 1 7.5 10.5H21l-2.7 7.3a2 2 0 0 1-1.9 1.2z"/>',
  image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
  save:'<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
  download:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5M12 15V3"/>',
  undo:'<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo:'<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  paste:'<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
  cut:'<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12"/>',
  copy:'<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  select:'<rect x="3" y="3" width="18" height="18" rx="1" stroke-dasharray="3 3"/>',
  lasso:'<path d="M7 22a5 5 0 0 1-2-4"/><path d="M3.3 14A6.8 6.8 0 0 1 2 10c0-4.4 4.5-8 10-8s10 3.6 10 8-4.5 8-10 8a12 12 0 0 1-5-1"/><circle cx="5" cy="16" r="2"/>',
  selall:'<rect x="3" y="3" width="18" height="18" stroke-dasharray="3 3"/><rect x="7" y="7" width="10" height="10" fill="currentColor" stroke="none" opacity=".4"/>',
  trash:'<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  trans:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 12h9V3M12 12h9v9h-9z" fill="currentColor" opacity=".35"/>',
  crop:'<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
  resize:'<path d="M21 3 9 15"/><path d="M12 3H3v18h18v-9"/><path d="M16 3h5v5"/><path d="M14 15H9v-5"/>',
  rotate:'<path d="M21 12a9 9 0 1 1-9-9c2.5 0 4.9 1 6.7 2.7L21 8"/><path d="M21 3v5h-5"/>',
  rotateL:'<path d="M3 12a9 9 0 1 0 9-9 9.7 9.7 0 0 0-6.7 2.7L3 8"/><path d="M3 3v5h5"/>',
  flipH:'<path d="M8 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3M16 3h3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-3M12 20v2M12 14v2M12 8v2M12 2v2"/>',
  flipV:'<path d="M21 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v3M21 16v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3M4 12H2M10 12H8M16 12h-2M22 12h-2"/>',
  invert:'<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  pencil:'<path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/><path d="m15 5 4 4"/>',
  fill:'<path d="m19 11-8-8-8.6 8.6a2 2 0 0 0 0 2.8l5.2 5.2c.8.8 2 .8 2.8 0L19 11z"/><path d="m5 2 5 5M2 13h15"/><path d="M22 20a2 2 0 1 1-4 0c0-1.6 1.7-2.4 2-4 .3 1.6 2 2.4 2 4z"/>',
  text:'<path d="M4 7V4h16v3M9 20h6M12 4v16"/>',
  eraser:'<path d="m7 21-4.3-4.3c-1-1-1-2.5 0-3.4l9.6-9.6c1-1 2.5-1 3.4 0l5.6 5.6c1 1 1 2.5 0 3.4L13 21"/><path d="M22 21H7M5 11l9 9"/>',
  picker:'<path d="m2 22 1-1h3l9-9"/><path d="M3 21v-3l9-9"/><path d="m15 6 3.4-3.4a2.1 2.1 0 1 1 3 3L18 9l.4.4a2.1 2.1 0 1 1-3 3l-3.8-3.8a2.1 2.1 0 1 1 3-3l.4.4z"/>',
  zoomin:'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3M11 8v6M8 11h6"/>',
  zoomout:'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3M8 11h6"/>',
  brush:'<path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>',
  outline:'<rect x="4" y="4" width="16" height="16" rx="2"/>',
  shapefill:'<rect x="4" y="4" width="16" height="16" rx="2" fill="currentColor" opacity=".45"/>',
  swap:'<path d="m16 3 4 4-4 4M20 7H4M8 21l-4-4 4-4M4 17h16"/>',
  palette:'<circle cx="13.5" cy="6.5" r="1.5"/><circle cx="17.5" cy="10.5" r="1.5"/><circle cx="8.5" cy="7.5" r="1.5"/><circle cx="6.5" cy="12.5" r="1.5"/><path d="M12 2a10 10 0 0 0 0 20c.9 0 1.7-.8 1.7-1.7 0-.4-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1 0-.9.8-1.7 1.7-1.7h2A5.6 5.6 0 0 0 22 11c0-5-4.5-9-10-9z"/>',
  fit:'<path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  one:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M10 9l2-1.5V16"/>',
  grid:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
  canvas:'<rect x="3" y="3" width="13" height="13" rx="1"/><path d="M20 8v12H8M16 16l5 5"/>',
};
$$('svg[data-icon]').forEach(s => { s.setAttribute('viewBox', '0 0 24 24'); s.innerHTML = ICONS[s.dataset.icon] || ''; });

// ── Elements & state ─────────────────────────────────────────
const ws = $('#ws'), stage = $('#stage');
const doc = $('#doc'), dctx = doc.getContext('2d', {willReadFrequently: true});
const prev = $('#preview'), pctx = prev.getContext('2d');
const selbox = $('#selbox'), shapebox = $('#shapebox'), ring = $('#ring'), ghost = $('#ghost'), gridEl = $('#grid');
for (const b of [selbox, shapebox]) for (const h of ['nw','n','ne','e','se','s','sw','w']) {
  const d = document.createElement('div'); d.className = 'h'; d.dataset.h = h; b.appendChild(d);
}

let W = 1, H = 1, zoom = 1;
let tool = 'pencil', prevTool = 'pencil';
let brushStyle = 'brush', shapeType = 'rect', outlineMode = 'solid', fillMode = 'none';
let selMode = 'rect', transparentSel = false, eraseTrans = false, fillTol = 10, showGrid = false;
const sizes = {pencil: 1, brush: 6, eraser: 12, shape: 3};
const colors = ['#000000', '#ffffff'];
let activeSlot = 0;
const font = {family: 'Arial', size: 28, bold: false, italic: false, underline: false, strike: false, opaque: false};
let fileName = CFG.saveName || 'untitled.png';
let dirty = false;
const savedNames = new Set();

let drag = null;          // active pointer operation {move(p,e), up(p,e), cancel()}
let sel = null;           // {x,y,w,h, path, lifted, canvas}
let shape = null;         // pending bbox shape {type, x,y,w,h, fx,fy}
let curve = null;         // {stage, p0,p3,c1,c2}
let poly = null;          // {pts:[]}
let textBox = null;       // {x,y, el, ta}
let internalClip = null;  // fallback when the system clipboard is unavailable
let spaceDown = false;

// ── Utilities ────────────────────────────────────────────────
function toast(msg, err) {
  const t = $('#toast'); t.textContent = msg; t.classList.toggle('err', !!err); t.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove('show'), err ? 5000 : 2600);
}
function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
function rgbToHex(r, g, b) { return '#' + [r, g, b].map(v => v.toString(16).padStart(2, '0')).join(''); }
function newCanvas(w, h) { const c = document.createElement('canvas'); c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h)); return c; }
function copyCanvas(src) { const c = newCanvas(src.width, src.height); c.getContext('2d').drawImage(src, 0, 0); return c; }
function isTyping(e) { const t = e.target; return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable); }
function stemOf(n) { const i = n.lastIndexOf('.'); return i > 0 ? n.slice(0, i) : n; }
function extOf(n) { const i = n.lastIndexOf('.'); return i > 0 ? n.slice(i).toLowerCase() : ''; }
function normBox(x0, y0, x1, y1) { return {x: Math.min(x0, x1), y: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0)}; }
function inBox(p, b, pad = 0) { return p.x >= b.x - pad && p.y >= b.y - pad && p.x <= b.x + b.w + pad && p.y <= b.y + b.h + pad; }

function markDirty() { if (!dirty) { dirty = true; updateTitle(); } }
function updateTitle() {
  $('#fname').textContent = (dirty ? '● ' : '') + fileName;
  document.title = (dirty ? '* ' : '') + fileName + ' — Devbits.Draw';
}

// ── History ──────────────────────────────────────────────────
const undoStack = [], redoStack = [];
function snapshot() { return {w: W, h: H, data: dctx.getImageData(0, 0, W, H)}; }
function pushUndo() {
  undoStack.push(snapshot()); redoStack.length = 0;
  const limit = clamp(Math.floor(600e6 / (W * H * 4)), 4, 60);
  while (undoStack.length > limit) undoStack.shift();
  markDirty(); updateHistoryButtons();
}
function restoreSnap(s) { setDocSize(s.w, s.h); dctx.putImageData(s.data, 0, 0); }
function undo() {
  if (drag) return;
  if (textBox) { cancelText(); return; }
  finalize();
  if (!undoStack.length) return;
  redoStack.push(snapshot()); restoreSnap(undoStack.pop()); markDirty(); updateHistoryButtons();
}
function redo() {
  if (drag) return;
  finalize();
  if (!redoStack.length) return;
  undoStack.push(snapshot()); restoreSnap(redoStack.pop()); markDirty(); updateHistoryButtons();
}
function updateHistoryButtons() { $('#btnUndo').disabled = !undoStack.length && !textBox; $('#btnRedo').disabled = !redoStack.length; }

// ── Document / view ──────────────────────────────────────────
function setDocSize(w, h) {
  // Resizing a canvas clears it; callers redraw afterwards.
  W = doc.width = prev.width = w; H = doc.height = prev.height = h;
  layout();
}
function resizeCanvasKeep(w, h, fill) {
  const old = copyCanvas(doc), ow = W, oh = H;
  setDocSize(w, h);
  dctx.drawImage(old, 0, 0);
  if (fill) {
    dctx.fillStyle = fill;
    if (w > ow) dctx.fillRect(ow, 0, w - ow, h);
    if (h > oh) dctx.fillRect(0, oh, Math.min(ow, w), h - oh);
  }
}
function loadDocument(src, name) {
  cancelAll();
  setDocSize(src.width, src.height);
  dctx.clearRect(0, 0, W, H); dctx.drawImage(src, 0, 0);
  undoStack.length = redoStack.length = 0; updateHistoryButtons();
  if (name) fileName = name;
  dirty = false; updateTitle();
  fitZoom(true);
}
function blankDocument(w, h, bg) {
  const c = newCanvas(w, h), x = c.getContext('2d');
  if (bg) { x.fillStyle = bg; x.fillRect(0, 0, w, h); }
  loadDocument(c);
}

function layout() {
  const cw = W * zoom, ch = H * zoom;
  stage.style.width = cw + 'px'; stage.style.height = ch + 'px';
  for (const c of [doc, prev]) { c.style.width = cw + 'px'; c.style.height = ch + 'px'; }
  stage.classList.toggle('px', zoom >= 2);
  const g = showGrid && zoom >= 4;
  gridEl.style.display = g ? 'block' : 'none';
  if (g) {
    gridEl.style.backgroundImage = 'linear-gradient(to right,rgba(128,128,128,.55) 1px,transparent 1px),linear-gradient(to bottom,rgba(128,128,128,.55) 1px,transparent 1px)';
    gridEl.style.backgroundSize = `${zoom}px ${zoom}px`;
  }
  const hs = $$('.cvh');
  hs[0].style.left = cw + 1 + 'px'; hs[0].style.top = ch / 2 - 4 + 'px';
  hs[1].style.left = cw / 2 - 4 + 'px'; hs[1].style.top = ch + 1 + 'px';
  hs[2].style.left = cw + 1 + 'px'; hs[2].style.top = ch + 1 + 'px';
  placeBox(selbox, sel); placeBox(shapebox, shape);
  layoutText();
  $('#stSize').textContent = `${W} × ${H} px`;
  $('#stZoom').textContent = Math.round(zoom * 100) + '%';
  $('#zoomSlider').value = Math.round(Math.log2(zoom) * 100);
}
function placeBox(el, b) {
  if (!b) { el.style.display = 'none'; return; }
  el.style.display = 'block';
  el.style.left = b.x * zoom + 'px'; el.style.top = b.y * zoom + 'px';
  el.style.width = Math.max(1, b.w * zoom) + 'px'; el.style.height = Math.max(1, b.h * zoom) + 'px';
  if (el === selbox) $('#stSel').textContent = `${Math.round(b.w)} × ${Math.round(b.h)} px`;
}
function setZoom(z, cx, cy) {
  z = clamp(z, 0.05, 32);
  const wr = ws.getBoundingClientRect();
  if (cx == null) { cx = wr.left + wr.width / 2; cy = wr.top + wr.height / 2; }
  const r = stage.getBoundingClientRect();
  const dx = (cx - r.left) / zoom, dy = (cy - r.top) / zoom;
  zoom = z; layout();
  const r2 = stage.getBoundingClientRect();
  ws.scrollLeft += r2.left + dx * zoom - cx; ws.scrollTop += r2.top + dy * zoom - cy;
}
const ZOOMS = [0.05, 0.1, 0.125, 0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1, 1.5, 2, 3, 4, 5, 6, 8, 12, 16, 24, 32];
function zoomStep(dir, cx, cy) {
  const next = dir > 0 ? ZOOMS.find(z => z > zoom + 1e-6) : [...ZOOMS].reverse().find(z => z < zoom - 1e-6);
  if (next) setZoom(next, cx, cy);
}
function fitZoom(capAt1) {
  const z = Math.min((ws.clientWidth - 110) / W, (ws.clientHeight - 110) / H);
  zoom = clamp(capAt1 ? Math.min(1, z) : z, 0.05, 32); layout();
  ws.scrollLeft = (ws.scrollWidth - ws.clientWidth) / 2; ws.scrollTop = (ws.scrollHeight - ws.clientHeight) / 2;
}
function toDoc(e) { const r = stage.getBoundingClientRect(); return {x: (e.clientX - r.left) / zoom, y: (e.clientY - r.top) / zoom}; }
function viewOrigin() {
  const r = stage.getBoundingClientRect(), wr = ws.getBoundingClientRect();
  return {x: clamp(Math.round((wr.left - r.left) / zoom), 0, W - 1), y: clamp(Math.round((wr.top - r.top) / zoom), 0, H - 1)};
}
function clearPreview() { pctx.clearRect(0, 0, W, H); prev.style.opacity = ''; prev.style.filter = ''; }

// ── Colors ───────────────────────────────────────────────────
const BASIC = ['#000000','#7f7f7f','#880015','#ed1c24','#ff7f27','#fff200','#22b14c','#00a2e8','#3f48cc','#a349a4',
               '#ffffff','#c3c3c3','#b97a57','#ffaec9','#ffc90e','#efe4b0','#b5e61d','#99d9ea','#7092be','#c8bfe7'];
let custom = [];
try { custom = JSON.parse(localStorage.getItem('devbits.draw.custom') || '[]').slice(0, 10); } catch (_) { custom = []; }
function renderPalette() {
  const pal = $('#palette'); pal.innerHTML = '';
  const all = BASIC.concat(custom, Array(10 - custom.length).fill(null));
  for (const c of all) {
    const d = document.createElement('div'); d.className = 'p' + (c ? '' : ' empty');
    if (c) { d.style.background = c; d.title = c; d.dataset.c = c; }
    pal.appendChild(d);
  }
}
function setColor(slot, c) {
  colors[slot] = c;
  $('#sw1').style.background = colors[0]; $('#sw2').style.background = colors[1];
  onStyleChange();
}
function setActiveSlot(i) { activeSlot = i; $('#slot1').classList.toggle('active', i === 0); $('#slot2').classList.toggle('active', i === 1); }
$('#palette').addEventListener('mousedown', e => {
  const c = e.target.dataset.c; if (!c) return;
  e.preventDefault(); setColor(e.button === 2 ? 1 : activeSlot, c);
});
$('#palette').addEventListener('contextmenu', e => e.preventDefault());
$('#slot1').onclick = () => setActiveSlot(0);
$('#slot2').onclick = () => setActiveSlot(1);
$('#colorInput').addEventListener('input', e => setColor(activeSlot, e.target.value));
$('#colorInput').addEventListener('change', e => {
  const c = e.target.value;
  if (!BASIC.includes(c) && !custom.includes(c)) {
    custom.push(c); if (custom.length > 10) custom.shift();
    try { localStorage.setItem('devbits.draw.custom', JSON.stringify(custom)); } catch (_) {}
    renderPalette();
  }
});
// Called whenever color / size / style settings change: live-update anything still editable.
function onStyleChange() {
  if (shape || curve || poly) renderShapePreview();
  if (sel && sel.lifted) renderFloat();
  if (textBox) styleText();
  updateRing();
}

// ── Tools & options UI ───────────────────────────────────────
const TOOL_NAMES = {pencil: 'Pencil', brush: 'Brush', eraser: 'Eraser', fill: 'Fill', text: 'Text', picker: 'Color picker',
  zoom: 'Magnifier', select: 'Select', shape: 'Shapes'};
const CURSORS = {pencil: 'crosshair', brush: 'crosshair', eraser: 'crosshair', fill: 'cell', text: 'text', picker: 'crosshair',
  zoom: 'zoom-in', select: 'crosshair', shape: 'crosshair'};
function sizeKey() { return {pencil: 'pencil', brush: 'brush', eraser: 'eraser', shape: 'shape'}[tool]; }
function curSize() { return sizes[sizeKey()] || 1; }
function setTool(t) {
  if (t !== tool) {
    finalize();
    if (t === 'picker' && tool !== 'picker') prevTool = tool;
    tool = t;
  }
  $$('[data-tool]').forEach(b => b.classList.toggle('active', b.dataset.tool === tool));
  $$('#shapes .tb').forEach(b => b.classList.toggle('active', tool === 'shape' && b.dataset.shape === shapeType));
  $('#toolName').textContent = tool === 'brush' ? BRUSHES.find(b => b[0] === brushStyle)[1]
    : tool === 'shape' ? SHAPE_NAMES[shapeType] : TOOL_NAMES[tool];
  $$('.opt[data-for]').forEach(o => o.classList.toggle('show', o.dataset.for === tool));
  const k = sizeKey();
  $('#optSize').classList.toggle('show', !!k);
  if (k) { $('#size').value = Math.min(100, sizes[k]); $('#sizeNum').value = sizes[k]; }
  $('#shapeHint').textContent = shapeType === 'curve' ? 'Drag a line, then click-drag twice to bend it · Shift snaps to 45°'
    : shapeType === 'polygon' ? 'Drag the first side, then click each corner · double-click or click the start to close'
    : 'Shift keeps proportions · adjust the handles until you click outside';
  ws.style.cursor = CURSORS[tool];
  updateRing();
}
function setSize(v) {
  const k = sizeKey(); if (!k) return;
  sizes[k] = clamp(Math.round(v) || 1, 1, 500);
  $('#size').value = Math.min(100, sizes[k]); $('#sizeNum').value = sizes[k];
  onStyleChange();
}
$('#size').addEventListener('input', e => setSize(+e.target.value));
$('#sizeNum').addEventListener('change', e => setSize(+e.target.value));
$('#tol').addEventListener('input', e => { fillTol = +e.target.value; $('#tolVal').textContent = fillTol + '%'; });
$('#eraseTrans').addEventListener('change', e => { eraseTrans = e.target.checked; });
$('#transSel').addEventListener('change', e => setTransparentSel(e.target.checked));
function setTransparentSel(v) {
  transparentSel = v; $('#transSel').checked = v; $('#miTrans').classList.toggle('on', v);
  if (sel && sel.lifted) renderFloat();
}
function setSelMode(m) {
  selMode = m;
  $$('#selMode button').forEach(b => b.classList.toggle('on', b.dataset.v === m));
  $$('[data-selmode]').forEach(b => b.classList.toggle('on', b.dataset.selmode === m));
  $('#selIcon').innerHTML = ICONS[m === 'free' ? 'lasso' : 'select'];
}
$$('#selMode button').forEach(b => b.onclick = () => { setSelMode(b.dataset.v); setTool('select'); });

const BRUSHES = [['brush', 'Brush'], ['calli1', 'Calligraphy brush 1'], ['calli2', 'Calligraphy brush 2'], ['air', 'Airbrush'],
  ['oil', 'Oil brush'], ['crayon', 'Crayon'], ['marker', 'Marker'], ['npencil', 'Natural pencil'], ['water', 'Watercolor brush']];
(function buildBrushes() {
  const m = $('#mBrush'), s = $('#brushSel');
  for (const [k, n] of BRUSHES) {
    const b = document.createElement('button'); b.dataset.brush = k;
    b.innerHTML = `<svg class="i" viewBox="0 0 24 24">${ICONS.brush}</svg>${n}`; m.appendChild(b);
    const o = document.createElement('option'); o.value = k; o.textContent = n; s.appendChild(o);
  }
  s.onchange = () => setBrush(s.value);
})();
function setBrush(k) {
  brushStyle = k; $('#brushSel').value = k;
  $$('[data-brush]').forEach(b => b.classList.toggle('on', b.dataset.brush === k));
  setTool('brush');
}
function setOutline(v) { outlineMode = v; $$('[data-outline]').forEach(b => b.classList.toggle('on', b.dataset.outline === v)); onStyleChange(); }
function setFill(v) { fillMode = v; $$('[data-fill]').forEach(b => b.classList.toggle('on', b.dataset.fill === v)); onStyleChange(); }

const FONTS = ['Arial', 'Calibri', 'Segoe UI', 'Verdana', 'Tahoma', 'Trebuchet MS', 'Times New Roman', 'Georgia', 'Garamond',
  'Courier New', 'Consolas', 'Comic Sans MS', 'Impact', 'Microsoft JhengHei', 'PMingLiU', 'DFKai-SB', 'Microsoft YaHei',
  'SimSun', 'Meiryo', 'Malgun Gothic', 'PingFang TC', 'Noto Sans TC', 'sans-serif', 'serif', 'monospace'];
(function buildFonts() {
  const s = $('#fontFam');
  for (const f of FONTS) { const o = document.createElement('option'); o.value = f; o.textContent = f; o.style.fontFamily = `"${f}"`; s.appendChild(o); }
  s.onchange = () => { font.family = s.value; styleText(); };
  $('#fontSize').onchange = e => { font.size = clamp(+e.target.value || 28, 4, 999); styleText(); };
  $$('#fontStyle button').forEach(b => b.onclick = () => {
    font[b.dataset.v] = !font[b.dataset.v]; b.classList.toggle('on', font[b.dataset.v]); styleText();
  });
  $('#textOpaque').onchange = e => { font.opaque = e.target.checked; styleText(); };
})();

// ── Shapes: geometry ─────────────────────────────────────────
const SHAPE_NAMES = {line: 'Line', curve: 'Curve', ellipse: 'Oval', rect: 'Rectangle', roundrect: 'Rounded rectangle',
  polygon: 'Polygon', triangle: 'Triangle', rtriangle: 'Right triangle', diamond: 'Diamond', pentagon: 'Pentagon',
  hexagon: 'Hexagon', arrowR: 'Right arrow', arrowL: 'Left arrow', arrowU: 'Up arrow', arrowD: 'Down arrow',
  star4: 'Four-point star', star5: 'Five-point star', star6: 'Six-point star', callout: 'Rectangular callout',
  callout2: 'Oval callout', heart: 'Heart', lightning: 'Lightning'};
function normPts(pts) {
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys), sx = Math.max(...xs) - x0, sy = Math.max(...ys) - y0;
  return pts.map(p => [(p[0] - x0) / sx, (p[1] - y0) / sy]);
}
function regular(n, start) { const a = []; for (let i = 0; i < n; i++) { const t = (start + i * 360 / n) * Math.PI / 180; a.push([Math.cos(t), Math.sin(t)]); } return normPts(a); }
function starPts(n, inner) { const a = []; for (let i = 0; i < n * 2; i++) { const t = (-90 + i * 180 / n) * Math.PI / 180, r = i % 2 ? inner : 1; a.push([r * Math.cos(t), r * Math.sin(t)]); } return normPts(a); }
const ARROW = [[0, .25], [.6, .25], [.6, 0], [1, .5], [.6, 1], [.6, .75], [0, .75]];
const POLYS = {
  triangle: [[.5, 0], [1, 1], [0, 1]], rtriangle: [[0, 0], [1, 1], [0, 1]], diamond: [[.5, 0], [1, .5], [.5, 1], [0, .5]],
  pentagon: regular(5, -90), hexagon: regular(6, 0),
  arrowR: ARROW, arrowL: ARROW.map(([x, y]) => [1 - x, y]), arrowU: ARROW.map(([x, y]) => [y, 1 - x]), arrowD: ARROW.map(([x, y]) => [y, x]),
  star4: starPts(4, .38), star5: starPts(5, .4), star6: starPts(6, .55),
  callout: [[0, 0], [1, 0], [1, .72], [.45, .72], [.22, 1], [.28, .72], [0, .72]],
  lightning: [[.4, 0], [.78, 0], [.56, .36], [.86, .36], [.24, 1], [.4, .52], [.12, .52]],
};
function tracePath(c, type, x, y, w, h) {
  c.beginPath();
  if (type === 'rect') c.rect(x, y, w, h);
  else if (type === 'ellipse') c.ellipse(x + w / 2, y + h / 2, Math.max(w / 2, .01), Math.max(h / 2, .01), 0, 0, Math.PI * 2);
  else if (type === 'roundrect') {
    const r = Math.min(w, h) * .18;
    c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  } else if (type === 'heart') {
    const X = u => x + u * w, Y = v => y + v * h;
    c.moveTo(X(.5), Y(.25));
    c.bezierCurveTo(X(.5), Y(.1), X(.36), Y(0), X(.23), Y(0)); c.bezierCurveTo(X(.08), Y(0), X(0), Y(.13), X(0), Y(.3));
    c.bezierCurveTo(X(0), Y(.56), X(.3), Y(.76), X(.5), Y(1)); c.bezierCurveTo(X(.7), Y(.76), X(1), Y(.56), X(1), Y(.3));
    c.bezierCurveTo(X(1), Y(.13), X(.92), Y(0), X(.77), Y(0)); c.bezierCurveTo(X(.64), Y(0), X(.5), Y(.1), X(.5), Y(.25));
    c.closePath();
  } else if (type === 'callout2') {
    // ellipse body (top 78%) with a tail toward the bottom-left
    const cx = x + w / 2, cy = y + h * .39, rx = w / 2, ry = h * .39;
    const a1 = 1.95, a2 = 2.35;  // gap in the ellipse where the tail attaches
    c.ellipse(cx, cy, Math.max(rx, .01), Math.max(ry, .01), 0, a2, a1 + Math.PI * 2);
    c.lineTo(x + w * .18, y + h);
    c.closePath();
  } else {
    const pts = POLYS[type]; if (!pts) return;
    pts.forEach(([u, v], i) => i ? c.lineTo(x + u * w, y + v * h) : c.moveTo(x + u * w, y + v * h));
    c.closePath();
  }
}
function strokeStyleOn(c, lw) {
  c.lineWidth = lw; c.strokeStyle = colors[0]; c.lineJoin = 'round'; c.lineCap = 'round';
  if (outlineMode === 'dashed') { c.setLineDash([lw * 3, lw * 2]); c.lineCap = 'butt'; }
  else if (outlineMode === 'dotted') c.setLineDash([0.001, lw * 2]);
  else c.setLineDash([]);
}
function paintClosed(c) {
  if (fillMode !== 'none') { c.fillStyle = fillMode === 'c1' ? colors[0] : colors[1]; c.fill(); }
  if (outlineMode !== 'none') c.stroke();
}
function drawShapeOn(c, s) {
  const lw = sizes.shape;
  c.save(); strokeStyleOn(c, lw);
  if (lw % 2) c.translate(.5, .5);
  if (s.type === 'line') {
    if (outlineMode === 'none') c.setLineDash([]);
    const x0 = s.fx ? s.x + s.w : s.x, y0 = s.fy ? s.y + s.h : s.y;
    c.beginPath(); c.moveTo(x0, y0); c.lineTo(s.fx ? s.x : s.x + s.w, s.fy ? s.y : s.y + s.h); c.stroke();
  } else { tracePath(c, s.type, s.x, s.y, s.w, s.h); paintClosed(c); }
  c.restore();
}
function drawCurveOn(c, k) {
  const lw = sizes.shape; c.save(); strokeStyleOn(c, lw); if (outlineMode === 'none') c.setLineDash([]);
  if (lw % 2) c.translate(.5, .5);
  const c1 = k.c1 || k.p0, c2 = k.c2 || k.p3;
  c.beginPath(); c.moveTo(k.p0.x, k.p0.y); c.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, k.p3.x, k.p3.y); c.stroke(); c.restore();
}
function drawPolyOn(c, pg, closed) {
  const lw = sizes.shape; c.save(); strokeStyleOn(c, lw); if (lw % 2) c.translate(.5, .5);
  c.beginPath(); pg.pts.forEach((p, i) => i ? c.lineTo(p.x, p.y) : c.moveTo(p.x, p.y));
  if (closed) { c.closePath(); paintClosed(c); } else { if (outlineMode === 'none') c.setLineDash([]); c.stroke(); }
  c.restore();
}
(function buildShapes() {
  const box = $('#shapes');
  for (const t of Object.keys(SHAPE_NAMES)) {
    const b = document.createElement('button'); b.className = 'tb'; b.dataset.shape = t; b.title = SHAPE_NAMES[t];
    const cv = newCanvas(40, 40); const c = cv.getContext('2d');
    c.strokeStyle = '#c8c8e0'; c.lineWidth = 3; c.lineJoin = 'round'; c.lineCap = 'round';
    if (t === 'line') { c.beginPath(); c.moveTo(6, 34); c.lineTo(34, 6); c.stroke(); }
    else if (t === 'curve') { c.beginPath(); c.moveTo(5, 32); c.bezierCurveTo(12, 0, 26, 42, 35, 8); c.stroke(); }
    else if (t === 'polygon') { c.beginPath(); [[6, 30], [14, 6], [34, 12], [28, 22], [34, 34]].forEach((p, i) => i ? c.lineTo(...p) : c.moveTo(...p)); c.closePath(); c.stroke(); }
    else { tracePath(c, t, 5, 5, 30, 30); c.stroke(); }
    b.appendChild(cv); box.appendChild(b);
    b.onclick = () => { finalize(); shapeType = t; setTool('shape'); };
  }
})();

// ── Selection ────────────────────────────────────────────────
function selPath(s) {
  // Free-form path in doc coordinates, scaled to the current selection box.
  if (!s.pts) return null;
  const p = new Path2D(), sx = s.w / s.ow, sy = s.h / s.oh;
  s.pts.forEach((q, i) => { const x = s.x + (q.x - s.ox) * sx, y = s.y + (q.y - s.oy) * sy; i ? p.lineTo(x, y) : p.moveTo(x, y); });
  p.closePath(); return p;
}
function extractSel(s) {
  const c = newCanvas(s.w, s.h), x = c.getContext('2d');
  if (s.pts) { x.translate(-s.x, -s.y); x.clip(selPath(s)); x.translate(s.x, s.y); }
  x.drawImage(doc, s.x, s.y, s.w, s.h, 0, 0, s.w, s.h);
  return c;
}
function lift() {
  if (!sel || sel.lifted) return;
  pushUndo();
  sel.canvas = extractSel(sel);
  dctx.save(); dctx.fillStyle = colors[1];
  if (sel.pts) dctx.fill(selPath(sel)); else dctx.fillRect(sel.x, sel.y, sel.w, sel.h);
  dctx.restore();
  sel.pts = null; sel.lifted = true;
  renderFloat();
}
let keyCache = null;
function floatSource() {
  if (!transparentSel) return sel.canvas;
  const key = sel.canvas._v + colors[1];
  if (keyCache && keyCache.src === sel.canvas && keyCache.key === key) return keyCache.out;
  const out = copyCanvas(sel.canvas), x = out.getContext('2d');
  const img = x.getImageData(0, 0, out.width, out.height), d = img.data, [r, g, b] = hexToRgb(colors[1]);
  for (let i = 0; i < d.length; i += 4) if (d[i] === r && d[i + 1] === g && d[i + 2] === b) d[i + 3] = 0;
  x.putImageData(img, 0, 0);
  keyCache = {src: sel.canvas, key, out};
  return out;
}
function renderFloat() {
  clearPreview();
  if (!sel || !sel.lifted) return;
  pctx.imageSmoothingEnabled = !(sel.w === sel.canvas.width && sel.h === sel.canvas.height);
  pctx.drawImage(floatSource(), sel.x, sel.y, sel.w, sel.h);
}
function setSelCanvas(c) { c._v = (sel.canvas && sel.canvas._v || 0) + 1; sel.canvas = c; }
function commitSelection() {
  if (!sel) return;
  if (sel.lifted) {
    dctx.imageSmoothingEnabled = !(sel.w === sel.canvas.width && sel.h === sel.canvas.height);
    dctx.drawImage(floatSource(), sel.x, sel.y, sel.w, sel.h);
    dctx.imageSmoothingEnabled = true;
  }
  sel = null; clearPreview(); placeBox(selbox, null); $('#stSel').textContent = '';
}
function makeSel(b, pts) {
  // Clamp to the canvas; returns false for an empty selection.
  const x0 = clamp(Math.floor(b.x), 0, W), y0 = clamp(Math.floor(b.y), 0, H);
  const x1 = clamp(Math.ceil(b.x + b.w), 0, W), y1 = clamp(Math.ceil(b.y + b.h), 0, H);
  if (x1 - x0 < 1 || y1 - y0 < 1) return false;
  sel = {x: x0, y: y0, w: x1 - x0, h: y1 - y0, lifted: false, canvas: null};
  if (pts) Object.assign(sel, {pts, ox: sel.x, oy: sel.y, ow: sel.w, oh: sel.h});
  placeBox(selbox, sel); return true;
}
function selectAll() { setTool('select'); commitSelection(); makeSel({x: 0, y: 0, w: W, h: H}); }
function deleteSel() {
  if (!sel) return;
  if (!sel.lifted) {
    pushUndo(); dctx.save(); dctx.fillStyle = colors[1];
    if (sel.pts) dctx.fill(selPath(sel)); else dctx.fillRect(sel.x, sel.y, sel.w, sel.h);
    dctx.restore();
  }
  sel = null; clearPreview(); placeBox(selbox, null); $('#stSel').textContent = '';
}
function selContent() {
  // Current selection pixels as a canvas at the on-screen size.
  if (sel.lifted) { const c = newCanvas(sel.w, sel.h); c.getContext('2d').drawImage(floatSource(), 0, 0, sel.w, sel.h); return c; }
  return extractSel(sel);
}
// Transform the floating selection or, when nothing is selected, the whole picture.
function transformTarget(fn) {
  commitText(); commitShape(); commitCurve(); commitPoly();
  if (sel) {
    lift();
    const src = newCanvas(sel.w, sel.h); src.getContext('2d').drawImage(sel.canvas, 0, 0, sel.w, sel.h);
    const out = fn(src, null);
    setSelCanvas(out); sel.w = out.width; sel.h = out.height;
    placeBox(selbox, sel); renderFloat(); markDirty();
  } else {
    pushUndo();
    const out = fn(copyCanvas(doc), colors[1]);
    setDocSize(out.width, out.height); dctx.clearRect(0, 0, W, H); dctx.drawImage(out, 0, 0);
  }
}
function rotated(src, deg) {
  const q = deg === 180, out = q ? newCanvas(src.width, src.height) : newCanvas(src.height, src.width), c = out.getContext('2d');
  c.translate(out.width / 2, out.height / 2); c.rotate(deg * Math.PI / 180); c.drawImage(src, -src.width / 2, -src.height / 2);
  return out;
}
function flipped(src, horiz) {
  const out = newCanvas(src.width, src.height), c = out.getContext('2d');
  if (horiz) { c.translate(src.width, 0); c.scale(-1, 1); } else { c.translate(0, src.height); c.scale(1, -1); }
  c.drawImage(src, 0, 0); return out;
}
function inverted(src) {
  const out = copyCanvas(src), c = out.getContext('2d'), img = c.getImageData(0, 0, out.width, out.height), d = img.data;
  for (let i = 0; i < d.length; i += 4) { d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2]; }
  c.putImageData(img, 0, 0); return out;
}
function scaledSkewed(src, nw, nh, sxDeg, syDeg, bg) {
  let s = src;
  if (nw !== src.width || nh !== src.height) {
    s = newCanvas(nw, nh); const c = s.getContext('2d'); c.imageSmoothingQuality = 'high'; c.drawImage(src, 0, 0, nw, nh);
  }
  if (!sxDeg && !syDeg) return s;
  const tx = Math.tan(sxDeg * Math.PI / 180), ty = Math.tan(syDeg * Math.PI / 180);
  const corners = [[0, 0], [nw, 0], [0, nh], [nw, nh]].map(([x, y]) => [x + tx * y, y + ty * x]);
  const minX = Math.min(...corners.map(p => p[0])), minY = Math.min(...corners.map(p => p[1]));
  const out = newCanvas(Math.max(...corners.map(p => p[0])) - minX, Math.max(...corners.map(p => p[1])) - minY), c = out.getContext('2d');
  if (bg) { c.fillStyle = bg; c.fillRect(0, 0, out.width, out.height); }
  c.setTransform(1, ty, tx, 1, -minX, -minY); c.drawImage(s, 0, 0);
  return out;
}
function crop() {
  if (!sel) { toast('Make a selection first, then crop.'); return; }
  commitText();
  const content = selContent(), wasLifted = sel.lifted, hadPath = !!sel.pts;
  if (!wasLifted) pushUndo();
  sel = null; clearPreview(); placeBox(selbox, null); $('#stSel').textContent = '';
  setDocSize(content.width, content.height);
  if (hadPath) { dctx.fillStyle = colors[1]; dctx.fillRect(0, 0, W, H); } else dctx.clearRect(0, 0, W, H);
  dctx.drawImage(content, 0, 0); markDirty();
}

// ── Pending shape / curve / polygon ──────────────────────────
function renderShapePreview() {
  clearPreview();
  if (shape) drawShapeOn(pctx, shape);
  if (curve) drawCurveOn(pctx, curve);
  if (poly) drawPolyOn(pctx, poly, false);
  placeBox(shapebox, shape && !shape.drawing ? shape : null);
}
function commitShape() {
  if (!shape) return;
  pushUndo(); drawShapeOn(dctx, shape); shape = null; clearPreview(); placeBox(shapebox, null);
}
function commitCurve() { if (!curve) return; pushUndo(); drawCurveOn(dctx, curve); curve = null; clearPreview(); }
function commitPoly(closed = true) {
  if (!poly) return;
  if (poly.pts.length >= 2) { pushUndo(); drawPolyOn(dctx, poly, closed && poly.pts.length >= 3); }
  poly = null; clearPreview();
}

// ── Text ─────────────────────────────────────────────────────
function fontCss(px) { return `${font.italic ? 'italic ' : ''}${font.bold ? 'bold ' : ''}${px}px "${font.family}", sans-serif`; }
function createText(x, y, w, h) {
  const el = document.createElement('div'); el.className = 'text-box';
  const ta = document.createElement('textarea'); ta.spellcheck = false; el.appendChild(ta);
  stage.appendChild(el);
  textBox = {x, y, w, h, el, ta};
  styleText(); layoutText();
  ta.addEventListener('input', autoGrow);
  el.addEventListener('pointerdown', e => {
    if (e.target === ta) return;
    // drag the dashed frame to move the box
    e.preventDefault(); e.stopPropagation();
    const sx = e.clientX, sy = e.clientY, ox = textBox.x, oy = textBox.y;
    el.setPointerCapture(e.pointerId);
    const mv = ev => { textBox.x = ox + (ev.clientX - sx) / zoom; textBox.y = oy + (ev.clientY - sy) / zoom; layoutText(); };
    const upH = () => { el.removeEventListener('pointermove', mv); el.removeEventListener('pointerup', upH); ta.focus(); };
    el.addEventListener('pointermove', mv); el.addEventListener('pointerup', upH);
  });
  new ResizeObserver(() => {
    if (!textBox || textBox.ta !== ta || textBox._layout) return;
    textBox.w = ta.offsetWidth / zoom; textBox.h = ta.offsetHeight / zoom;
  }).observe(ta);
  ta.focus();
  setTimeout(() => ta.focus(), 0);
  updateHistoryButtons();
}
function autoGrow() {
  const ta = textBox.ta;
  if (ta.scrollHeight > ta.clientHeight) { textBox.h = ta.scrollHeight / zoom; layoutText(); }
}
function styleText() {
  if (!textBox) return;
  const ta = textBox.ta;
  ta.style.font = fontCss(font.size * zoom); ta.style.lineHeight = '1.2';
  ta.style.color = colors[0];
  ta.style.background = font.opaque ? colors[1] : 'transparent';
  ta.style.textDecoration = [font.underline && 'underline', font.strike && 'line-through'].filter(Boolean).join(' ') || 'none';
  textBox.h = Math.max(textBox.h, font.size * 1.2);
  layoutText(); autoGrow();
}
function layoutText() {
  if (!textBox) return;
  const t = textBox; t._layout = true;
  t.el.style.left = t.x * zoom - 5 + 'px'; t.el.style.top = t.y * zoom - 5 + 'px';
  t.ta.style.width = t.w * zoom + 'px'; t.ta.style.height = t.h * zoom + 'px';
  t.ta.style.fontSize = font.size * zoom + 'px';
  requestAnimationFrame(() => { t._layout = false; });
}
function wrapLines(c, text, maxW) {
  const out = [];
  for (const para of text.split('\n')) {
    let line = '';
    // Break on spaces, or between any characters for CJK / over-long words.
    const tokens = para.match(/\s+|[⺀-鿿가-힯豈-﫿＀-￯]|[^\s⺀-鿿가-힯豈-﫿＀-￯]+/g) || [''];
    for (let tok of tokens) {
      if (c.measureText(line + tok).width <= maxW || !line) {
        if (!line && c.measureText(tok).width > maxW) {
          for (const ch of tok) { if (c.measureText(line + ch).width > maxW && line) { out.push(line); line = ''; } line += ch; }
        } else line += tok;
      } else { out.push(line.replace(/\s+$/, '')); line = /^\s+$/.test(tok) ? '' : tok; }
    }
    out.push(line);
  }
  return out;
}
function commitText() {
  if (!textBox) return;
  const t = textBox; textBox = null;
  const text = t.ta.value; t.el.remove();
  if (text.trim()) {
    pushUndo();
    const fs = font.size, lh = fs * 1.2;
    dctx.save(); dctx.font = fontCss(fs); dctx.textBaseline = 'middle';
    const lines = wrapLines(dctx, text, t.w + 0.5);
    if (font.opaque) { dctx.fillStyle = colors[1]; dctx.fillRect(t.x, t.y, t.w, Math.max(t.h, lines.length * lh)); }
    dctx.fillStyle = colors[0];
    lines.forEach((ln, i) => {
      const cy = t.y + i * lh + lh / 2;
      dctx.fillText(ln, t.x, cy);
      const lw = dctx.measureText(ln).width, th = Math.max(1, fs / 16);
      if (font.underline) dctx.fillRect(t.x, cy + fs * .42, lw, th);
      if (font.strike) dctx.fillRect(t.x, cy - th / 2, lw, th);
    });
    dctx.restore();
  }
  updateHistoryButtons();
}
function cancelText() { if (!textBox) return; textBox.el.remove(); textBox = null; updateHistoryButtons(); }

// Commit everything still editable (selection, text, pending shapes).
function finalize() { commitText(); commitShape(); commitCurve(); commitPoly(); commitSelection(); }
function cancelAll() {
  if (drag && drag.cancel) drag.cancel();
  drag = null; cancelText(); shape = curve = poly = sel = null; clearPreview(); placeBox(selbox, null); placeBox(shapebox, null);
}

// ── Painting primitives ──────────────────────────────────────
function line(a, b, fn) {
  // Bresenham over integer pixels
  let x0 = Math.floor(a.x), y0 = Math.floor(a.y); const x1 = Math.floor(b.x), y1 = Math.floor(b.y);
  const dx = Math.abs(x1 - x0), dy = -Math.abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  for (;;) { fn(x0, y0); if (x0 === x1 && y0 === y1) break; const e2 = 2 * err; if (e2 >= dy) { err += dy; x0 += sx; } if (e2 <= dx) { err += dx; y0 += sy; } }
}
function walk(a, b, step, fn) {
  const d = Math.hypot(b.x - a.x, b.y - a.y), n = Math.max(1, Math.ceil(d / step));
  for (let i = 1; i <= n; i++) fn(a.x + (b.x - a.x) * i / n, a.y + (b.y - a.y) * i / n);
}
function noisePattern(c, color, density, alpha) {
  const t = newCanvas(64, 64), x = t.getContext('2d'), img = x.createImageData(64, 64), [r, g, b] = hexToRgb(color);
  for (let i = 0; i < 64 * 64; i++) if (Math.random() < density) img.data.set([r, g, b, 255 * alpha * (.55 + .45 * Math.random())], i * 4);
  x.putImageData(img, 0, 0);
  return c.createPattern(t, 'repeat');
}
function snap45(a, p) {
  const dx = p.x - a.x, dy = p.y - a.y, ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * Math.PI / 4, len = Math.hypot(dx, dy);
  return {x: a.x + Math.cos(ang) * len, y: a.y + Math.sin(ang) * len};
}

function startStroke(p, e, right) {
  const color = colors[right ? 1 : 0], s = curSize();
  if (tool === 'pencil') {
    pushUndo(); dctx.fillStyle = color;
    const o = Math.floor(s / 2), dot = (x, y) => dctx.fillRect(x - o, y - o, s, s);
    let last = p; dot(Math.floor(p.x), Math.floor(p.y));
    const origin = p;
    return {move(q, ev) { if (ev.shiftKey) { q = snap45(origin, q); } line(last, q, dot); last = q; }, up() {}};
  }
  if (tool === 'eraser') {
    pushUndo();
    const o = Math.floor(s / 2);
    if (right) {
      // color eraser: swap Color 1 for Color 2 under the square
      const [r1, g1, b1] = hexToRgb(colors[0]), [r2, g2, b2] = hexToRgb(colors[1]);
      const rub = (x, y) => {
        const x0 = clamp(x - o, 0, W), y0 = clamp(y - o, 0, H), w = clamp(x - o + s, 0, W) - x0, h = clamp(y - o + s, 0, H) - y0;
        if (w < 1 || h < 1) return;
        const img = dctx.getImageData(x0, y0, w, h), d = img.data;
        for (let i = 0; i < d.length; i += 4)
          if (Math.abs(d[i] - r1) < 24 && Math.abs(d[i + 1] - g1) < 24 && Math.abs(d[i + 2] - b1) < 24 && d[i + 3]) { d[i] = r2; d[i + 1] = g2; d[i + 2] = b2; }
        dctx.putImageData(img, x0, y0);
      };
      let last = p; rub(Math.floor(p.x), Math.floor(p.y));
      return {move(q) { walk(last, q, Math.max(1, s / 3), (x, y) => rub(Math.floor(x), Math.floor(y))); last = q; }, up() {}};
    }
    dctx.fillStyle = colors[1];
    const rub = (x, y) => eraseTrans ? dctx.clearRect(x - o, y - o, s, s) : dctx.fillRect(x - o, y - o, s, s);
    let last = p; rub(Math.floor(p.x), Math.floor(p.y));
    return {move(q) { line(last, q, rub); last = q; }, up() {}};
  }
  // brushes
  const st = brushStyle;
  if (st === 'marker' || st === 'water') {
    // Draw opaque into the preview layer, then composite once so the stroke doesn't darken where it overlaps itself.
    clearPreview();
    const alpha = st === 'marker' ? .5 : .32, width = st === 'water' ? s * 1.6 : s;
    prev.style.opacity = alpha; if (st === 'water') prev.style.filter = `blur(${Math.max(1, width * .12) * zoom}px)`;
    pctx.strokeStyle = pctx.fillStyle = color; pctx.lineWidth = width; pctx.lineCap = st === 'marker' ? 'square' : 'round'; pctx.lineJoin = 'round';
    const pts = [p];
    const redraw = () => { pctx.clearRect(0, 0, W, H); pctx.beginPath(); pts.forEach((q, i) => i ? pctx.lineTo(q.x, q.y) : pctx.moveTo(q.x, q.y)); if (pts.length === 1) pctx.lineTo(p.x + .01, p.y); pctx.stroke(); };
    redraw();
    return {
      move(q) { pts.push(q); redraw(); },
      up() {
        pushUndo(); dctx.save(); dctx.globalAlpha = alpha;
        if (st === 'water') dctx.filter = `blur(${Math.max(1, width * .12)}px)`;
        dctx.drawImage(prev, 0, 0); dctx.restore(); clearPreview();
      },
      cancel() { clearPreview(); },
    };
  }
  pushUndo();
  dctx.save();
  let last = p;
  const end = () => dctx.restore();
  if (st === 'brush') {
    dctx.strokeStyle = dctx.fillStyle = color; dctx.lineWidth = s; dctx.lineCap = dctx.lineJoin = 'round';
    dctx.beginPath(); dctx.arc(p.x, p.y, s / 2, 0, Math.PI * 2); dctx.fill();
    return {move(q) { dctx.beginPath(); dctx.moveTo(last.x, last.y); dctx.lineTo(q.x, q.y); dctx.stroke(); last = q; }, up: end, cancel: end};
  }
  if (st === 'calli1' || st === 'calli2') {
    const k = s / 2 / Math.SQRT2, n = st === 'calli1' ? {x: k, y: -k} : {x: k, y: k};
    dctx.fillStyle = dctx.strokeStyle = color; dctx.lineWidth = 1;
    const seg = (a, b) => { dctx.beginPath(); dctx.moveTo(a.x + n.x, a.y + n.y); dctx.lineTo(a.x - n.x, a.y - n.y); dctx.lineTo(b.x - n.x, b.y - n.y); dctx.lineTo(b.x + n.x, b.y + n.y); dctx.closePath(); dctx.fill(); dctx.stroke(); };
    seg(p, p);
    return {move(q) { seg(last, q); last = q; }, up: end, cancel: end};
  }
  if (st === 'air') {
    dctx.fillStyle = color;
    const r = Math.max(3, s * 1.5);
    const spray = () => { const n = Math.ceil(r * 1.2); for (let i = 0; i < n; i++) { const a = Math.random() * Math.PI * 2, d = Math.sqrt(Math.random()) * r; dctx.fillRect(Math.floor(last.x + Math.cos(a) * d), Math.floor(last.y + Math.sin(a) * d), 1, 1); } };
    spray(); const timer = setInterval(spray, 25);
    const stop = () => { clearInterval(timer); end(); };
    return {move(q) { last = q; spray(); }, up: stop, cancel: stop};
  }
  if (st === 'oil') {
    const n = clamp(Math.round(s * .8), 5, 40), bristles = [];
    for (let i = 0; i < n; i++) { const a = Math.random() * Math.PI * 2, d = Math.sqrt(Math.random()) * s / 2; bristles.push({x: Math.cos(a) * d, y: Math.sin(a) * d, al: .25 + Math.random() * .6}); }
    dctx.strokeStyle = color; dctx.lineCap = 'round'; dctx.lineWidth = Math.max(1, s / 7);
    const seg = (a, b) => { for (const br of bristles) { dctx.globalAlpha = br.al; dctx.beginPath(); dctx.moveTo(a.x + br.x, a.y + br.y); dctx.lineTo(b.x + br.x + .01, b.y + br.y); dctx.stroke(); } };
    seg(p, p);
    return {move(q) { seg(last, q); last = q; }, up: end, cancel: end};
  }
  // crayon / natural pencil: a grainy pattern anchored to the canvas keeps the texture as strokes overlap
  const pencilLike = st === 'npencil';
  dctx.strokeStyle = noisePattern(dctx, color, pencilLike ? .5 : .62, pencilLike ? .75 : .95);
  dctx.lineWidth = pencilLike ? Math.max(1, s / 2) : s; dctx.lineCap = dctx.lineJoin = 'round';
  dctx.beginPath(); dctx.moveTo(p.x, p.y); dctx.lineTo(p.x + .01, p.y); dctx.stroke();
  return {move(q) { dctx.beginPath(); dctx.moveTo(last.x, last.y); dctx.lineTo(q.x, q.y); dctx.stroke(); last = q; }, up: end, cancel: end};
}

function floodFill(x0, y0, rgb, tol) {
  const img = dctx.getImageData(0, 0, W, H), d = img.data, seen = new Uint8Array(W * H);
  const i0 = (y0 * W + x0) * 4, t0 = d[i0], t1 = d[i0 + 1], t2 = d[i0 + 2], t3 = d[i0 + 3], th = tol * 2.55;
  if (!tol && t0 === rgb[0] && t1 === rgb[1] && t2 === rgb[2] && t3 === 255) return false;
  const match = q => { const i = q * 4; return !seen[q] && Math.abs(d[i] - t0) <= th && Math.abs(d[i + 1] - t1) <= th && Math.abs(d[i + 2] - t2) <= th && Math.abs(d[i + 3] - t3) <= th; };
  const stack = [x0, y0];
  while (stack.length) {
    const y = stack.pop(), x = stack.pop(), row = y * W;
    if (!match(row + x)) continue;
    let l = x, r = x;
    while (l > 0 && match(row + l - 1)) l--;
    while (r < W - 1 && match(row + r + 1)) r++;
    for (let i = l; i <= r; i++) { const q = row + i; seen[q] = 1; d[q * 4] = rgb[0]; d[q * 4 + 1] = rgb[1]; d[q * 4 + 2] = rgb[2]; d[q * 4 + 3] = 255; }
    for (const ny of [y - 1, y + 1]) {
      if (ny < 0 || ny >= H) continue;
      let run = false;
      for (let i = l; i <= r; i++) { const m = match(ny * W + i); if (m && !run) { stack.push(i, ny); run = true; } else if (!m) run = false; }
    }
  }
  dctx.putImageData(img, 0, 0); return true;
}

// ── Box drag helpers (selection & pending shape) ─────────────
function boxDrag(b, handle, p0, onChange, keepRatio) {
  const o = {...b};
  return (q, ev) => {
    let dx = q.x - p0.x, dy = q.y - p0.y;
    if (!handle) { b.x = Math.round(o.x + dx); b.y = Math.round(o.y + dy); onChange(); return; }
    let {x, y, w, h} = o;
    if (handle.includes('w')) { x += dx; w -= dx; }
    if (handle.includes('e')) w += dx;
    if (handle.includes('n')) { y += dy; h -= dy; }
    if (handle.includes('s')) h += dy;
    if ((keepRatio || ev.shiftKey) && handle.length === 2 && o.w && o.h) {
      const sc = Math.max(w / o.w, h / o.h); const nw = o.w * sc, nh = o.h * sc;
      if (handle.includes('w')) x = o.x + o.w - nw; if (handle.includes('n')) y = o.y + o.h - nh; w = nw; h = nh;
    }
    if (w < 1) { if (handle.includes('w')) x = o.x + o.w - 1; w = 1; }
    if (h < 1) { if (handle.includes('n')) y = o.y + o.h - 1; h = 1; }
    Object.assign(b, {x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h)}); onChange();
  };
}

// ── Pointer handling ─────────────────────────────────────────
function onDown(e, p, right) {
  const t = e.target;
  // canvas edge handles
  const cv = t.closest && t.closest('.cvh');
  if (cv) {
    finalize();
    const mode = cv.dataset.cv, r = stage.getBoundingClientRect();
    ghost.style.display = 'block';
    let nw = W, nh = H;
    const show = () => { ghost.style.width = nw * zoom + 'px'; ghost.style.height = nh * zoom + 'px'; $('#stSel').textContent = `${nw} × ${nh} px`; };
    show();
    return {
      move(q) { if (mode !== 'b') nw = Math.max(1, Math.round(q.x)); if (mode !== 'r') nh = Math.max(1, Math.round(q.y)); show(); },
      up() { ghost.style.display = 'none'; $('#stSel').textContent = ''; if (nw !== W || nh !== H) { pushUndo(); resizeCanvasKeep(nw, nh, colors[1]); } },
      cancel() { ghost.style.display = 'none'; },
    };
  }
  if (textBox) { commitText(); return null; }

  if (tool === 'select') {
    if (sel && t.closest && t.closest('#selbox')) {
      const handle = t.dataset.h || null;
      lift();
      if (e.ctrlKey && !handle) dctx.drawImage(floatSource(), sel.x, sel.y, sel.w, sel.h);  // leave a copy behind
      const mv = boxDrag(sel, handle, p, () => { placeBox(selbox, sel); renderFloat(); });
      return {move: mv, up() {}};
    }
    commitSelection();
    if (selMode === 'free') {
      const pts = [p];
      const draw = () => { clearPreview(); pctx.save(); pctx.setLineDash([4 / zoom, 4 / zoom]); pctx.lineWidth = 1 / zoom; pctx.strokeStyle = '#3b82f6'; pctx.beginPath(); pts.forEach((q, i) => i ? pctx.lineTo(q.x, q.y) : pctx.moveTo(q.x, q.y)); pctx.stroke(); pctx.restore(); };
      return {
        move(q) { pts.push(q); draw(); },
        up() {
          clearPreview(); if (pts.length < 3) return;
          const xs = pts.map(q => q.x), ys = pts.map(q => q.y);
          const b = {x: Math.min(...xs), y: Math.min(...ys)}; b.w = Math.max(...xs) - b.x; b.h = Math.max(...ys) - b.y;
          makeSel(b, pts.map(q => ({x: q.x, y: q.y})));
        },
        cancel() { clearPreview(); },
      };
    }
    const p0 = p;
    return {
      move(q) { const b = normBox(p0.x, p0.y, clamp(q.x, 0, W), clamp(q.y, 0, H)); placeBox(selbox, {x: Math.floor(b.x), y: Math.floor(b.y), w: Math.round(b.w), h: Math.round(b.h)}); },
      up(q) { placeBox(selbox, null); makeSel(normBox(p0.x, p0.y, clamp(q.x, 0, W), clamp(q.y, 0, H))); },
      cancel() { placeBox(selbox, null); },
    };
  }

  if (tool === 'shape') {
    if (shape && t.closest && t.closest('#shapebox')) {
      const mv = boxDrag(shape, t.dataset.h || null, p, renderShapePreview);
      return {move: mv, up() { renderShapePreview(); }};
    }
    commitShape();
    if (shapeType === 'curve') {
      if (curve && curve.stage) {
        const key = curve.stage === 1 ? 'c1' : 'c2';
        curve[key] = p; if (curve.stage === 1) curve.c2 = p; renderShapePreview();
        return {move(q) { curve[key] = q; if (key === 'c1') curve.c2 = q; renderShapePreview(); }, up() { if (curve.stage === 2) commitCurve(); else curve.stage = 2; }};
      }
      curve = {stage: 0, p0: p, p3: p};
      return {
        move(q, ev) { curve.p3 = ev.shiftKey ? snap45(curve.p0, q) : q; renderShapePreview(); },
        up() { if (Math.hypot(curve.p3.x - curve.p0.x, curve.p3.y - curve.p0.y) < 1) { curve = null; clearPreview(); } else curve.stage = 1; },
        cancel() { curve = null; clearPreview(); },
      };
    }
    if (shapeType === 'polygon') {
      if (poly) {
        const first = poly.pts[0];
        if (Math.hypot(p.x - first.x, p.y - first.y) * zoom < 8 && poly.pts.length >= 3) { commitPoly(true); return null; }
        poly.pts.push(p); renderShapePreview();
        return {move(q, ev) { poly.pts[poly.pts.length - 1] = ev.shiftKey ? snap45(poly.pts[poly.pts.length - 2], q) : q; renderShapePreview(); }, up() {}};
      }
      poly = {pts: [p, p]};
      return {
        move(q, ev) { poly.pts[1] = ev.shiftKey ? snap45(p, q) : q; renderShapePreview(); },
        up() { const [a, b] = poly.pts; if (Math.hypot(b.x - a.x, b.y - a.y) < 1) { poly = null; clearPreview(); } },
        cancel() { poly = null; clearPreview(); },
      };
    }
    const p0 = {x: Math.round(p.x), y: Math.round(p.y)};
    shape = {type: shapeType, x: p0.x, y: p0.y, w: 0, h: 0, fx: false, fy: false, drawing: true};
    return {
      move(q, ev) {
        let x1 = Math.round(q.x), y1 = Math.round(q.y);
        if (ev.shiftKey) {
          if (shape.type === 'line') { const s = snap45(p0, q); x1 = Math.round(s.x); y1 = Math.round(s.y); }
          else { const d = Math.max(Math.abs(x1 - p0.x), Math.abs(y1 - p0.y)); x1 = p0.x + Math.sign(x1 - p0.x || 1) * d; y1 = p0.y + Math.sign(y1 - p0.y || 1) * d; }
        }
        Object.assign(shape, normBox(p0.x, p0.y, x1, y1), {fx: x1 < p0.x, fy: y1 < p0.y});
        renderShapePreview();
      },
      up() { if (shape.w < 1 && shape.h < 1) { shape = null; clearPreview(); } else shape.drawing = false; renderShapePreview(); },
      cancel() { shape = null; clearPreview(); },
    };
  }

  commitSelection();
  if (tool === 'fill') {
    const x = Math.floor(p.x), y = Math.floor(p.y);
    if (x < 0 || y < 0 || x >= W || y >= H) return null;
    const snap = snapshot();
    if (floodFill(x, y, hexToRgb(colors[right ? 1 : 0]), fillTol)) { undoStack.push(snap); redoStack.length = 0; markDirty(); updateHistoryButtons(); }
    return null;
  }
  if (tool === 'picker') {
    const pick = q => {
      const x = Math.floor(q.x), y = Math.floor(q.y);
      if (x < 0 || y < 0 || x >= W || y >= H) return;
      const d = dctx.getImageData(x, y, 1, 1).data; setColor(right ? 1 : 0, rgbToHex(d[0], d[1], d[2]));
    };
    pick(p);
    return {move: pick, up() { setTool(prevTool); }};
  }
  if (tool === 'zoom') { if (right) zoomStep(-1, e.clientX, e.clientY); else zoomStep(1, e.clientX, e.clientY); return null; }
  if (tool === 'text') {
    const p0 = p;
    return {
      move(q) { const b = normBox(p0.x, p0.y, q.x, q.y); ghost.style.display = 'block'; ghost.style.left = b.x * zoom + 'px'; ghost.style.top = b.y * zoom + 'px'; ghost.style.width = b.w * zoom + 'px'; ghost.style.height = b.h * zoom + 'px'; },
      up(q) {
        ghost.style.display = 'none'; ghost.style.left = ghost.style.top = '0';
        const b = normBox(p0.x, p0.y, q.x, q.y);
        if (b.w * zoom < 16) { b.w = Math.max(160, font.size * 8) ; }
        createText(b.x, b.y, b.w, Math.max(b.h, font.size * 1.2));
      },
      cancel() { ghost.style.display = 'none'; },
    };
  }
  return startStroke(p, e, right);
}

ws.addEventListener('pointerdown', e => {
  if (e.target.closest('.text-box')) return;
  if (e.button === 1 || (spaceDown && e.button === 0)) {
    e.preventDefault();
    const sx = e.clientX, sy = e.clientY, sl = ws.scrollLeft, st = ws.scrollTop;
    ws.setPointerCapture(e.pointerId); ws.style.cursor = 'grabbing';
    drag = {move(_, ev) { ws.scrollLeft = sl - (ev.clientX - sx); ws.scrollTop = st - (ev.clientY - sy); }, up() { ws.style.cursor = CURSORS[tool]; }};
    return;
  }
  if (e.button !== 0 && e.button !== 2) return;
  if (drag) return;
  e.preventDefault();
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  ws.setPointerCapture(e.pointerId);
  const d = onDown(e, toDoc(e), e.button === 2);
  drag = d || null;
});
ws.addEventListener('pointermove', e => {
  const p = toDoc(e);
  const inside = p.x >= 0 && p.y >= 0 && p.x < W && p.y < H;
  $('#stPos').textContent = inside ? `${Math.floor(p.x)}, ${Math.floor(p.y)} px` : ' ';
  moveRing(e, inside);
  if (drag) {
    const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
    for (const ev of (evs.length ? evs : [e])) drag.move(toDoc(ev), ev);
  }
});
function endDrag(e) {
  if (!drag) return;
  const d = drag; drag = null;
  d.up(toDoc(e), e);
  if (tool === 'shape') renderShapePreview();
  updateHistoryButtons();
}
ws.addEventListener('pointerup', endDrag);
ws.addEventListener('pointercancel', () => { if (drag) { const d = drag; drag = null; if (d.cancel) d.cancel(); else d.up({x: 0, y: 0}, {}); } });
ws.addEventListener('contextmenu', e => e.preventDefault());
ws.addEventListener('dblclick', e => { if (tool === 'shape' && poly) { poly.pts.pop(); commitPoly(true); } });
ws.addEventListener('pointerleave', () => { ring.style.display = 'none'; $('#stPos').textContent = ' '; });
ws.addEventListener('wheel', e => {
  if (!e.ctrlKey) return;
  e.preventDefault();
  setZoom(zoom * Math.pow(1.0015, -e.deltaY), e.clientX, e.clientY);
}, {passive: false});
$('#zoomSlider').addEventListener('input', e => setZoom(Math.pow(2, e.target.value / 100)));

// brush-size outline following the pointer
let ringPos = null;
function moveRing(e, inside) { ringPos = inside ? toDoc(e) : null; updateRing(); }
function updateRing() {
  const s = curSize(), show = ringPos && (tool === 'brush' || tool === 'eraser' || (tool === 'pencil' && s > 2)) && s * zoom >= 5;
  if (!show) { ring.style.display = 'none'; return; }
  const d = (tool === 'brush' && brushStyle === 'air') ? Math.max(3, s * 1.5) * 2 : s;
  ring.style.display = 'block';
  ring.style.borderRadius = tool === 'brush' ? '50%' : '0';
  const px = tool === 'brush' ? ringPos.x : Math.floor(ringPos.x) - Math.floor(s / 2) + d / 2;
  const py = tool === 'brush' ? ringPos.y : Math.floor(ringPos.y) - Math.floor(s / 2) + d / 2;
  ring.style.width = ring.style.height = d * zoom + 'px';
  ring.style.left = (px - d / 2) * zoom + 'px'; ring.style.top = (py - d / 2) * zoom + 'px';
}

// ── Files, clipboard, saving ─────────────────────────────────
async function decodeFile(file) {
  try { return await createImageBitmap(file); }
  catch (_) {
    // Formats the browser can't read (TIFF, some ICO/BMP): let the server convert.
    const r = await fetch('/api/decode', {method: 'POST', body: file});
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || 'Unsupported image');
    return await createImageBitmap(await r.blob());
  }
}
function confirmDiscard() { return !dirty || confirm('Discard unsaved changes?'); }
async function openFile(file) {
  if (!file || !confirmDiscard()) return;
  try {
    const bmp = await decodeFile(file);
    savedNames.clear();
    const ext = Object.keys(SAVE_EXTS).includes(extOf(file.name)) ? extOf(file.name) : '.png';
    loadDocument(bmp, stemOf(file.name) + '_drawn' + ext);
    toast(`Opened ${file.name} (${W} × ${H}). Save writes ${fileName}.`);
  } catch (err) { toast('Could not open image: ' + err.message, true); }
}
function pasteCanvas(c) {
  finalize();
  pushUndo();
  if (c.width > W || c.height > H) resizeCanvasKeep(Math.max(W, c.width), Math.max(H, c.height), colors[1]);
  setTool('select');
  const o = viewOrigin();
  sel = {x: Math.min(o.x, W - c.width), y: Math.min(o.y, H - c.height), w: c.width, h: c.height, lifted: true, canvas: null};
  setSelCanvas(c);
  placeBox(selbox, sel); renderFloat();
}
async function insertFile(file) {
  if (!file) return;
  try { const bmp = await decodeFile(file); pasteCanvas(copyCanvas(bmp)); toast('Photo added — drag to move, handles to resize, click outside to place.'); }
  catch (err) { toast('Could not add image: ' + err.message, true); }
}
$('#fileOpen').onchange = e => { openFile(e.target.files[0]); e.target.value = ''; };
$('#fileInsert').onchange = e => { insertFile(e.target.files[0]); e.target.value = ''; };

async function copySel(cut) {
  if (!sel) { toast('Nothing is selected.'); return; }
  const c = selContent(); internalClip = c;
  try {
    const blob = await new Promise(r => c.toBlob(r, 'image/png'));
    await navigator.clipboard.write([new ClipboardItem({'image/png': blob})]);
  } catch (_) { /* internalClip still works inside this page */ }
  if (cut) deleteSel();
}
async function pasteFromButton() {
  try {
    for (const item of await navigator.clipboard.read()) {
      const type = item.types.find(t => t.startsWith('image/'));
      if (type) { pasteCanvas(copyCanvas(await createImageBitmap(await item.getType(type)))); return; }
    }
  } catch (_) {}
  if (internalClip) pasteCanvas(copyCanvas(internalClip)); else toast('The clipboard has no image.');
}
document.addEventListener('paste', async e => {
  if (isTyping(e)) return;
  const items = Array.from(e.clipboardData ? e.clipboardData.items : []);
  const it = items.find(i => i.type.startsWith('image/'));
  e.preventDefault();
  if (it) { try { pasteCanvas(copyCanvas(await createImageBitmap(it.getAsFile()))); } catch (err) { toast('Could not paste: ' + err.message, true); } }
  else if (internalClip) pasteCanvas(copyCanvas(internalClip));
  else toast('The clipboard has no image.');
});
ws.addEventListener('dragover', e => { e.preventDefault(); ws.classList.add('drop'); });
ws.addEventListener('dragleave', () => ws.classList.remove('drop'));
ws.addEventListener('drop', e => {
  e.preventDefault(); ws.classList.remove('drop');
  const f = Array.from(e.dataTransfer.files).find(f => f.type.startsWith('image/') || /\.(tiff?|ico|bmp|p[gpbn]m)$/i.test(f.name));
  if (f) insertFile(f);
});

const SAVE_EXTS = {'.png': 1, '.jpg': 1, '.jpeg': 1, '.bmp': 1, '.gif': 1, '.webp': 1, '.tif': 1, '.tiff': 1, '.ico': 1, '.pgm': 1, '.ppm': 1};
function docBlob() { return new Promise(r => doc.toBlob(r, 'image/png')); }
async function save(name) {
  finalize();
  name = name || fileName;
  const blob = await docBlob();
  const post = ow => fetch(`/api/save?name=${encodeURIComponent(name)}&overwrite=${ow ? 1 : 0}`, {method: 'POST', body: blob});
  try {
    let r = await post(savedNames.has(name));
    if (r.status === 409) {
      if (!confirm(`${name} already exists.\nDo you want to replace it?`)) return false;
      r = await post(true);
    }
    const j = await r.json();
    if (!r.ok) { toast('Save failed: ' + (j.error || r.status), true); return false; }
    fileName = j.name; savedNames.add(j.name); dirty = false; updateTitle();
    toast('Saved ' + j.path);
    return true;
  } catch (err) { toast('Save failed: ' + err.message + ' (is the devbits server still running?)', true); return false; }
}
function saveAs() {
  const d = $('#dlgSave'), f = d.querySelector('form');
  f.stem.value = stemOf(fileName);
  const ext = extOf(fileName) === '.jpeg' ? '.jpg' : extOf(fileName) === '.tif' ? '.tiff' : extOf(fileName);
  f.ext.value = SAVE_EXTS[ext] ? ext : '.png';
  $('#saveDir').textContent = CFG.saveDir || '(current folder)';
  openDialog(d, () => { if (f.stem.value.trim()) save(f.stem.value.trim() + f.ext.value); });
  f.stem.select();
}
async function download() {
  finalize();
  const ext = extOf(fileName), type = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}[ext] || 'image/png';
  let src = doc;
  if (type === 'image/jpeg') { src = newCanvas(W, H); const c = src.getContext('2d'); c.fillStyle = '#fff'; c.fillRect(0, 0, W, H); c.drawImage(doc, 0, 0); }
  const blob = await new Promise(r => src.toBlob(r, type, .95));
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = type === 'image/png' ? stemOf(fileName) + '.png' : fileName; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

// ── Dialogs ──────────────────────────────────────────────────
function openDialog(d, onOk) {
  d.returnValue = '';
  d.onclose = () => { if (d.returnValue === 'ok') onOk(); };
  d.showModal();
}
function dlgNew() {
  const d = $('#dlgNew'), f = d.querySelector('form');
  f.w.value = W; f.h.value = H;
  openDialog(d, () => {
    if (!confirmDiscard()) return;
    const w = clamp(+f.w.value | 0, 1, 20000), h = clamp(+f.h.value | 0, 1, 20000);
    blankDocument(w, h, f.bg.value === 'none' ? null : f.bg.value === 'c2' ? colors[1] : '#ffffff');
    fileName = 'untitled.png'; savedNames.clear(); updateTitle();
  });
}
function dlgProps() {
  finalize();
  const d = $('#dlgProps'), f = d.querySelector('form');
  f.w.value = W; f.h.value = H;
  openDialog(d, () => {
    const w = clamp(+f.w.value | 0, 1, 20000), h = clamp(+f.h.value | 0, 1, 20000);
    if (w !== W || h !== H) { pushUndo(); resizeCanvasKeep(w, h, colors[1]); }
  });
}
function dlgResize() {
  commitText(); commitShape(); commitCurve(); commitPoly();
  const d = $('#dlgResize'), f = d.querySelector('form');
  const bw = sel ? sel.w : W, bh = sel ? sel.h : H;
  $('#rsTarget').textContent = sel ? '— selection' : '— whole picture';
  const fillFields = () => { if (f.by.value === 'pct') { f.rw.value = 100; f.rh.value = 100; } else { f.rw.value = bw; f.rh.value = bh; } };
  f.by.value = 'pct'; fillFields(); f.sx.value = 0; f.sy.value = 0;
  f.querySelectorAll('input[name=by]').forEach(r => r.onchange = fillFields);
  f.rw.oninput = () => { if (f.keep.checked) f.rh.value = f.by.value === 'pct' ? f.rw.value : Math.round(f.rw.value * bh / bw); };
  f.rh.oninput = () => { if (f.keep.checked) f.rw.value = f.by.value === 'pct' ? f.rh.value : Math.round(f.rh.value * bw / bh); };
  openDialog(d, () => {
    const pct = f.by.value === 'pct';
    const nw = clamp(Math.round(pct ? bw * f.rw.value / 100 : +f.rw.value), 1, 20000);
    const nh = clamp(Math.round(pct ? bh * f.rh.value / 100 : +f.rh.value), 1, 20000);
    const sx = clamp(+f.sx.value || 0, -89, 89), sy = clamp(+f.sy.value || 0, -89, 89);
    if (nw === bw && nh === bh && !sx && !sy) return;
    transformTarget((src, bg) => scaledSkewed(src, nw, nh, sx, sy, bg));
  });
}

// ── Actions / menus ──────────────────────────────────────────
const ACTIONS = {
  new: dlgNew, open: () => $('#fileOpen').click(), insert: () => $('#fileInsert').click(),
  save: () => save(), saveas: saveAs, download,
  undo, redo, paste: pasteFromButton, cut: () => copySel(true), copy: () => copySel(false),
  crop, resize: dlgResize, selectall: selectAll, delete: deleteSel, transsel: () => setTransparentSel(!transparentSel),
  rotR: () => transformTarget(s => rotated(s, 90)), rotL: () => transformTarget(s => rotated(s, -90)), rot180: () => transformTarget(s => rotated(s, 180)),
  flipH: () => transformTarget(s => flipped(s, true)), flipV: () => transformTarget(s => flipped(s, false)), invert: () => transformTarget(s => inverted(s)),
  swap: () => { const [a, b] = colors; setColor(0, b); setColor(1, a); },
  editcolor: () => { const i = $('#colorInput'); i.value = colors[activeSlot]; i.click(); },
  zoomin: () => zoomStep(1), zoomout: () => zoomStep(-1), zoom100: () => setZoom(1), fit: () => fitZoom(false),
  grid: () => { showGrid = !showGrid; $('#btnGrid').classList.toggle('active', showGrid); layout(); if (showGrid && zoom < 4) toast('Gridlines appear at 400% zoom and above.'); },
  props: dlgProps,
};
function closeMenus() { $$('.menu').forEach(m => m.classList.remove('show')); }
document.addEventListener('click', e => {
  const btn = e.target.closest('button');
  if (btn && !btn.closest('dialog')) btn.blur();
  const act = e.target.closest('[data-act]');
  const menuBtn = e.target.closest('[data-menu]');
  const inMenu = e.target.closest('.menu');
  if (menuBtn && !inMenu) {
    const m = $('#' + menuBtn.dataset.menu), was = m.classList.contains('show');
    closeMenus();
    if (menuBtn.dataset.tool) setTool(menuBtn.dataset.tool);
    if (!was) { const r = menuBtn.getBoundingClientRect(); m.style.left = r.left + 'px'; m.style.top = r.bottom + 4 + 'px'; m.classList.add('show'); }
    return;
  }
  if (!inMenu) closeMenus();
  if (act) { closeMenus(); ACTIONS[act.dataset.act](); return; }
  const tb = e.target.closest('[data-tool]');
  if (tb) { setTool(tb.dataset.tool); return; }
  if (inMenu) {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.selmode) { setSelMode(b.dataset.selmode); setTool('select'); }
    if (b.dataset.brush) setBrush(b.dataset.brush);
    if (b.dataset.outline) setOutline(b.dataset.outline);
    if (b.dataset.fill) setFill(b.dataset.fill);
    closeMenus();
  }
});

// ── Keyboard ─────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && textBox && document.activeElement === textBox.ta) { commitText(); e.preventDefault(); return; }
  if (isTyping(e) || document.querySelector('dialog[open]')) return;
  const k = e.key.toLowerCase(), ctrl = e.ctrlKey || e.metaKey;
  if (e.key === ' ') { spaceDown = true; if (!drag) ws.style.cursor = 'grab'; e.preventDefault(); return; }
  if (ctrl) {
    const map = {z: e.shiftKey ? redo : undo, y: redo, s: e.shiftKey ? saveAs : () => save(), o: ACTIONS.open, a: selectAll,
      c: () => copySel(false), x: e.shiftKey ? crop : () => copySel(true), e: dlgProps, g: ACTIONS.grid,
      '=': ACTIONS.zoomin, '+': ACTIONS.zoomin, '-': ACTIONS.zoomout, '0': ACTIONS.fit, '1': ACTIONS.zoom100};
    if (map[k]) { e.preventDefault(); map[k](); }
    return;
  }
  if (drag) { if (e.key === 'Escape') { const d = drag; drag = null; if (d.cancel) d.cancel(); } return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { if (sel) { deleteSel(); e.preventDefault(); } return; }
  if (e.key === 'Escape') { if (poly) commitPoly(false); else finalize(); return; }
  if (e.key === 'Enter') { if (poly) commitPoly(true); else if (curve) commitCurve(); else finalize(); return; }
  if (e.key.startsWith('Arrow') && (sel || shape)) {
    e.preventDefault();
    const b = sel || shape, step = e.shiftKey ? 10 : 1;
    if (sel) lift();
    b.x += e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
    b.y += e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
    if (sel) { placeBox(selbox, sel); renderFloat(); } else renderShapePreview();
    return;
  }
  const tools = {p: 'pencil', b: 'brush', e: 'eraser', f: 'fill', i: 'picker', t: 'text', s: 'select', z: 'zoom', u: 'shape'};
  if (tools[k]) { setTool(tools[k]); return; }
  if (k === 'x') ACTIONS.swap();
  if (k === '[') setSize(curSize() - (curSize() > 10 ? 2 : 1));
  if (k === ']') setSize(curSize() + (curSize() >= 10 ? 2 : 1));
});
document.addEventListener('keyup', e => { if (e.key === ' ') { spaceDown = false; if (!drag) ws.style.cursor = CURSORS[tool]; } });
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

// ── Boot ─────────────────────────────────────────────────────
renderPalette();
setColor(0, '#000000'); setColor(1, '#ffffff');
setSelMode('rect'); setOutline('solid'); setFill('none'); setBrush('brush'); setTool('pencil');
updateTitle(); updateHistoryButtons();
(async function boot() {
  const [w, h] = CFG.size || [1280, 720];
  if (CFG.image) {
    try {
      const r = await fetch(CFG.image);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
      loadDocument(await createImageBitmap(await r.blob()));
      return;
    } catch (err) { toast('Could not load the image: ' + err.message, true); }
  }
  blankDocument(w, h, '#ffffff');
})();
window.addEventListener('resize', () => layout());
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Validate a Save file name: a bare name with a supported extension.

    Directories are not allowed — files always land in the save folder. A
    name without an extension gets ``.png``.
    """
    name = (name or "").strip()
    if (not name or name in (".", "..") or Path(name).name != name
            or any(ch in name for ch in '/\\:*?"<>|')):
        raise ValueError(f"Invalid file name: {name!r}")
    if not Path(name).suffix:
        name += ".png"
    suffix = Path(name).suffix.lower()
    if suffix not in _SAVE_FORMATS:
        supported = ", ".join(sorted(_SAVE_FORMATS))
        raise ValueError(f"Unsupported format {suffix!r}. Use one of: {supported}")
    return name


def _encode_image(data: bytes, target: Path) -> Path:
    """Decode ``data`` (any Pillow-readable image) and write it to ``target``.

    The format follows the extension. Formats without an alpha channel (JPEG,
    BMP, PGM/PPM) are flattened onto white; PGM is also converted to 8-bit gray. The file is written to a temporary name and
    moved into place, so a failed save never leaves a half-written file.
    """
    fmt = _SAVE_FORMATS.get(target.suffix.lower())
    if fmt is None:
        raise ValueError(f"Unsupported format: {target.suffix}")
    with Image.open(io.BytesIO(data)) as image:
        rgba = image.convert("RGBA")

    options: dict = {}
    out = rgba
    if fmt in ("JPEG", "BMP", "PPM"):
        out = Image.new("RGB", rgba.size, "white")
        out.paste(rgba, mask=rgba.getchannel("A"))
        if fmt == "JPEG":
            options = {"quality": 95}
        if target.suffix.lower() == ".pgm":
            out = out.convert("L")
    elif fmt == "WEBP":
        options = {"quality": 95}
    elif fmt == "ICO":
        # Icons are square: center the picture on a transparent square first.
        side = max(rgba.size)
        out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        out.paste(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2))
        if side < 16:
            out = out.resize((16, 16), Image.NEAREST)
        options = {"sizes": [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= max(16, side)]}

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        out.save(tmp, format=fmt, **options)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def _to_png(source) -> bytes:
    """Decode an image (path or file object) into PNG bytes the browser can show.

    EXIF orientation is applied so photos appear the right way up.
    """
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ("I", "I;16", "I;16B", "I;16L", "I;16N"):
            # 16-bit gray (e.g. PGM with maxval > 255, 16-bit PNG/TIFF): Pillow
            # holds 0-65535, which convert() would clip — scale to 8 bits instead.
            import numpy as np

            gray = np.asarray(image, dtype=np.float64) / 257
            image = Image.fromarray(gray.round().clip(0, 255).astype(np.uint8))
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA")
        buf = io.BytesIO()
        image.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _default_save_target(image_path: Path | None, output: Path | None) -> tuple[Path, str]:
    """Where Save writes: ``(folder, file name)``."""
    if output is not None:
        output = output.resolve()
        return output.parent, _safe_name(output.name)
    if image_path is not None:
        suffix = image_path.suffix.lower()
        if suffix not in _SAVE_FORMATS:
            suffix = ".png"
        return image_path.parent, f"{image_path.stem}_drawn{suffix}"
    return Path.cwd(), "untitled.png"


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _DrawHandler(BaseHTTPRequestHandler):
    """Request handler for the drawing editor."""

    image_path: Path | None = None
    save_dir: Path = Path.cwd()
    save_name: str = "untitled.png"
    canvas_size: tuple[int, int] = (1280, 720)

    def log_message(self, format, *args):  # noqa: A002
        pass  # silence console spam

    def do_GET(self):  # noqa: N802
        try:
            path = urlparse(self.path).path
            if path in ("/", ""):
                self._serve_html()
            elif path == "/api/initial":
                self._serve_initial()
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_POST(self):  # noqa: N802
        try:
            if not self._same_origin():
                self._json_response({"error": "Cross-origin request refused"}, 403)
                return
            url = urlparse(self.path)
            if url.path == "/api/save":
                self._handle_save(parse_qs(url.query))
            elif url.path == "/api/decode":
                self._handle_decode()
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _same_origin(self) -> bool:
        # Browsers send Origin on cross-site POSTs; refuse other pages writing files.
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{self.headers.get('Host')}"

    def _serve_html(self):
        config = {
            "image": "/api/initial" if self.image_path else None,
            "saveName": self.save_name,
            "saveDir": str(self.save_dir),
            "size": list(self.canvas_size),
        }
        inject = "<script>window.__DRAW__ = " + json.dumps(config).replace("</", "<\\/") + ";</script>"
        self._send(200, _HTML.replace("</head>", inject + "\n</head>", 1).encode("utf-8"), "text/html; charset=utf-8")

    def _serve_initial(self):
        if self.image_path is None:
            self.send_error(404)
            return
        try:
            data = _to_png(self.image_path)
        except Exception as exc:
            self._json_response({"error": str(exc)}, 500)
            return
        self._send(200, data, "image/png")

    def _read_body(self) -> bytes | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json_response({"error": "Empty request body"}, 400)
            return None
        if length > _MAX_BODY:
            self._json_response({"error": "Image is too large"}, 413)
            return None
        return self.rfile.read(length)

    def _handle_save(self, query: dict):
        try:
            name = _safe_name(query.get("name", [""])[0])
        except ValueError as exc:
            self._json_response({"error": str(exc)}, 400)
            return
        target = self.save_dir / name
        overwrite = query.get("overwrite", ["0"])[0] == "1"
        if target.exists() and not overwrite:
            self._json_response({"error": "exists", "path": str(target)}, 409)
            return
        data = self._read_body()
        if data is None:
            return
        try:
            _encode_image(data, target)
        except Exception as exc:
            self._json_response({"error": str(exc)}, 500)
            return
        self._json_response({"path": str(target), "name": name})

    def _handle_decode(self):
        data = self._read_body()
        if data is None:
            return
        try:
            png = _to_png(io.BytesIO(data))
        except Exception:
            self._json_response({"error": "Unrecognized image format"}, 415)
            return
        self._send(200, png, "image/png")

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, data: dict, code: int = 200):
        self._send(code, json.dumps(data).encode("utf-8"), "application/json")


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch_draw(
    image_path: Path | None = None,
    output: Path | None = None,
    size: tuple[int, int] = (1280, 720),
) -> None:
    """Launch the Draw web editor, optionally opening ``image_path``."""
    if image_path is not None:
        image_path = image_path.resolve()
        try:
            with Image.open(image_path) as image:
                image.verify()
        except Exception as exc:
            raise ValueError(f"Not a readable image: {image_path}") from exc

    save_dir, save_name = _default_save_target(image_path, output)

    _DrawHandler.image_path = image_path
    _DrawHandler.save_dir = save_dir
    _DrawHandler.save_name = save_name
    _DrawHandler.canvas_size = size

    port = _find_free_port()
    server = _ThreadedHTTPServer(("127.0.0.1", port), _DrawHandler)
    url = f"http://127.0.0.1:{port}"

    print(f"Draw editor running at {url}")
    print(f"Save writes to {save_dir / save_name}")
    print("Press Ctrl+C to stop.")

    threading.Timer(0.3, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
