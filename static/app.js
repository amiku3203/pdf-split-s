(function () {
  // ---------- shared elements ----------
  const toolTabs = document.getElementById('toolTabs');
  const splitForm = document.getElementById('splitForm');
  const mergeForm = document.getElementById('mergeForm');
  const convertForm = document.getElementById('convertForm');
  const compressForm = document.getElementById('compressForm');

  const status = document.getElementById('status');
  const statusText = document.getElementById('statusText');
  const errorPanel = document.getElementById('errorPanel');
  const errorText = document.getElementById('errorText');
  const emptyState = document.getElementById('emptyState');

  const resultsSection = document.getElementById('resultsSection');
  const resultsTitle = document.getElementById('resultsTitle');
  const resultsCount = document.getElementById('resultsCount');
  const cardsList = document.getElementById('cardsList');
  const resultsNotice = document.getElementById('resultsNotice');
  const downloadAllBtn = document.getElementById('downloadAllBtn');
  const jobTag = document.getElementById('jobTag');

  const STRIPES = ['var(--brass)', 'var(--sage)', 'var(--teal)', 'var(--dusty-rose)'];

  let activeTool = 'split';

  // ============================================================ TABS ===

  toolTabs.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-tool]');
    if (!btn) return;
    activeTool = btn.dataset.tool;
    [...toolTabs.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
    splitForm.classList.toggle('hidden', activeTool !== 'split');
    mergeForm.classList.toggle('hidden', activeTool !== 'merge');
    convertForm.classList.toggle('hidden', activeTool !== 'convert');
    compressForm.classList.toggle('hidden', activeTool !== 'compress');
    document.getElementById('pagePicker').classList.add('hidden');
    hideError();
    showEmpty();
    resultsTitle.textContent = activeTool === 'convert'
      ? 'Converted'
      : (activeTool === 'compress' ? 'Compressed' : 'Files ready');
  });

  // ============================================================ SPLIT ===

  const urlInput = document.getElementById('pdfUrl');
  const urlFieldLabel = document.getElementById('urlFieldLabel');
  const selectedFilePill = document.getElementById('selectedFilePill');
  const selectedFileName = document.getElementById('selectedFileName');
  const selectedFileSize = document.getElementById('selectedFileSize');
  const removeFileBtn = document.getElementById('removeFileBtn');
  const splitBtn = document.getElementById('splitBtn');
  const kwToggle = document.getElementById('kwToggle');
  const kwToggleWrap = document.getElementById('kwToggleWrap');
  const modeSelect = document.getElementById('modeSelect');
  const rangeInputWrap = document.getElementById('rangeInputWrap');
  const rangesInput = document.getElementById('rangesInput');
  const fixedInputWrap = document.getElementById('fixedInputWrap');
  const pagesPerChunkInput = document.getElementById('pagesPerChunkInput');
  const uploadAltBtn = document.getElementById('uploadAltBtn');
  const fileInput = document.getElementById('fileInput');

  const pagePicker = document.getElementById('pagePicker');
  const pageGrid = document.getElementById('pageGrid');
  const pagePickerCount = document.getElementById('pagePickerCount');
  const selectAllBtn = document.getElementById('selectAllBtn');
  const deselectAllBtn = document.getElementById('deselectAllBtn');
  const extractModeToggle = document.getElementById('extractModeToggle');
  const confirmPagesBtn = document.getElementById('confirmPagesBtn');
  const closePagePickerBtn = document.getElementById('closePagePickerBtn');

  let pagePickerJobId = null;
  let pagePickerTotal = 0;
  let selectedPages = new Set();
  let combinePages = false;
  let pickerKind = 'pages';      // 'pages' | 'sections'
  let lastClickedPage = null;
  let sectionsData = [];
  let sectionCrops = {};         // page -> [x0, y0, x1, y1] (fractions) for the section being built

  let keyword = 'Unit';
  let mode = 'unit';
  let pendingFile = null;

  const STATUS_MESSAGES = {
    unit: ['Reading the table of contents…', 'Finding every "{kw}" heading…', 'Cutting along the seams…', 'Stitching files together…'],
    lesson: ['Reading the table of contents…', 'Finding every numbered lesson…', 'Cutting along the seams…', 'Stitching lesson files together…'],
    combined: ['Reading the table of contents…', 'Finding every "{kw}" and its lessons…', 'Building a folder per unit…', 'Stitching everything together…'],
    unit_and_lesson: ['Reading the table of contents…', 'Finding every "{kw}" and its lessons…', 'Saving the whole unit and each lesson…', 'Stitching everything together…'],
    extract_text: ['Reading the table of contents…', 'Finding every "{kw}" and its lessons…', 'Pulling out the actual text…', 'Writing text files…'],
    combined_with_text: ['Reading the table of contents…', 'Finding every "{kw}" and its lessons…', 'Slicing pages and pulling out text…', 'Writing both file types…'],
    range: ['Reading page count…', 'Cutting the ranges you asked for…', 'Stitching files together…'],
    fixed: ['Reading page count…', 'Chunking every {n} pages…', 'Stitching files together…'],
    per_page: ['Reading page count…', 'Splitting every single page…', 'Stitching files together…'],
    nepali_text: ['Reading the PDF…', 'Converting Preeti / Kantipur text to Unicode…', 'Finding every एकाइ and पाठ…', 'Writing text files…'],
    nepali_spellcheck: ['Reading the PDF…', 'Converting Preeti / Kantipur text to Unicode…', 'Finding every एकाइ and पाठ…', 'Checking every word against the dictionary…'],
    nepali_pdf_text: ['Reading the PDF…', 'Converting Preeti / Kantipur text to Unicode…', 'Finding every एकाइ and पाठ…', 'Slicing pages and writing text…'],
  };

  const NEPALI_MODES = new Set(['nepali_text', 'nepali_pdf_text', 'nepali_spellcheck']);
  const MODES_HIDING_KEYWORD = new Set(['lesson', 'range', 'fixed', 'per_page', 'pages', 'sections', ...NEPALI_MODES]);

  const SAVED_MODE_KEY = 'pdf_toolkit_split_mode';

  modeSelect.addEventListener('change', () => {
    mode = modeSelect.value;
    try { localStorage.setItem(SAVED_MODE_KEY, mode); } catch (_) {}
    kwToggleWrap.classList.toggle('hidden', MODES_HIDING_KEYWORD.has(mode));
    rangeInputWrap.classList.toggle('hidden', mode !== 'range' && !NEPALI_MODES.has(mode));
    rangesInput.placeholder = NEPALI_MODES.has(mode)
      ? 'optional: e.g. 1-5, 6-10'
      : 'e.g. 1-5, 6-10, 11-end';
    fixedInputWrap.classList.toggle('hidden', mode !== 'fixed');
    splitBtn.innerHTML = (mode === 'pages' || mode === 'sections')
      ? '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 4l8 8-8 8" /></svg> Load pages'
      : '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 4l8 8-8 8" /></svg> Split';
    pagePicker.classList.add('hidden');
  });

  // Restore previous split mode on reload if available
  try {
    const savedMode = localStorage.getItem(SAVED_MODE_KEY);
    if (savedMode && [...modeSelect.options].some((o) => o.value === savedMode)) {
      modeSelect.value = savedMode;
      modeSelect.dispatchEvent(new Event('change'));
    }
  } catch (_) {}

  kwToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-kw]');
    if (!btn) return;
    keyword = btn.dataset.kw;
    [...kwToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
  });

  function setPendingFile(file) {
    if (!file) {
      pendingFile = null;
      if (selectedFilePill) selectedFilePill.classList.add('hidden');
      if (urlFieldLabel) urlFieldLabel.classList.remove('hidden');
      urlInput.placeholder = "Paste PDF link (or click 'Upload PDF' below)";
      return;
    }
    pendingFile = file;
    urlInput.value = '';
    if (selectedFilePill && selectedFileName && selectedFileSize) {
      selectedFileName.textContent = file.name;
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      selectedFileSize.textContent = file.size > 10 * 1024 * 1024
        ? `${mb} MB • Chunked Stream`
        : `${mb} MB`;
      selectedFilePill.classList.remove('hidden');
      if (urlFieldLabel) urlFieldLabel.classList.add('hidden');
    }
    hideError();
  }

  if (removeFileBtn) {
    removeFileBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      setPendingFile(null);
      fileInput.value = '';
    });
  }

  uploadAltBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files[0]) {
      setPendingFile(fileInput.files[0]);
    }
  });

  // Drag-and-drop PDF onto splitForm
  ['dragenter', 'dragover'].forEach((eventName) => {
    splitForm.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      splitForm.classList.add('drag-over');
    });
  });
  ['dragleave', 'drop'].forEach((eventName) => {
    splitForm.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      splitForm.classList.remove('drag-over');
    });
  });
  splitForm.addEventListener('drop', (e) => {
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      const f = e.dataTransfer.files[0];
      if (f.name.toLowerCase().endsWith('.pdf') || f.type === 'application/pdf') {
        setPendingFile(f);
      } else {
        showError('Please drop a valid PDF file.');
      }
    }
  });

  urlInput.addEventListener('input', () => {
    if (urlInput.value.trim()) {
      setPendingFile(null);
    }
  });

  async function handleSplitSubmit(e) {
    e.preventDefault();
    hideError();
    hideEmpty();
    resultsSection.style.display = 'none';

    if (!pendingFile && !urlInput.value.trim()) {
      fileInput.click();
      showError('Please choose a PDF file from your computer to begin.');
      return;
    }
    if (mode === 'range' && !rangesInput.value.trim()) {
      showError('Enter at least one page range, e.g. "1-5, 6-10".');
      return;
    }

    if (mode === 'pages' || mode === 'sections') {
      await handleLoadPages();
      return;
    }

    splitBtn.disabled = true;
    showStatus(STATUS_MESSAGES[mode], { kw: keyword, n: pagesPerChunkInput.value || 5 });

    try {
      let res;
      if (pendingFile) {
        let jobId;
        if (pendingFile.size > 10 * 1024 * 1024) {
          showStatus([`Preparing ${Math.round(pendingFile.size / (1024 * 1024))} MB upload…`]);
          jobId = await uploadFileInChunks(pendingFile, (pct) => {
            showStatus([`Uploading ${Math.round(pendingFile.size / (1024 * 1024))} MB… (${pct}%)`]);
          });
          showStatus(STATUS_MESSAGES[mode], { kw: keyword, n: pagesPerChunkInput.value || 5 });
          res = await fetch('/split', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              job_id: jobId,
              mode, keyword,
              ranges: rangesInput.value.trim(),
              pages_per_chunk: parseInt(pagesPerChunkInput.value || '5', 10),
            }),
          });
        } else {
          const fd = new FormData();
          fd.append('file', pendingFile);
          fd.append('mode', mode);
          fd.append('keyword', keyword);
          fd.append('ranges', rangesInput.value.trim());
          fd.append('pages_per_chunk', pagesPerChunkInput.value || 5);
          res = await fetch('/split', { method: 'POST', body: fd });
        }
      } else {
        res = await fetch('/split', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            pdf_url: urlInput.value.trim(),
            mode, keyword,
            ranges: rangesInput.value.trim(),
            pages_per_chunk: parseInt(pagesPerChunkInput.value || '5', 10),
          }),
        });
      }
      await handleJobResponse(res);
    } catch (err) {
      showError(err.message || String(err));
      showEmpty();
    } finally {
      hideStatus();
      splitBtn.disabled = false;
    }
  }

  async function safeJson(res) {
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (_) {
      const match = text.match(/<title>(.*?)<\/title>/i) || text.match(/<h1>(.*?)<\/h1>/i) || text.match(/<p>(.*?)<\/p>/i);
      const title = match ? match[1].replace(/<[^>]+>/g, '').trim() : '';
      throw new Error(title || `Server error (${res.status}) — please check the server terminal.`);
    }
    if (!res.ok) {
      throw new Error(data.error || `Error ${res.status}: Something went wrong.`);
    }
    return data;
  }

  async function uploadFileInChunks(file, onProgress) {
    const CHUNK_SIZE = 10 * 1024 * 1024; // 10 MB per chunk
    const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
    const randomBytes = new Uint8Array(6);
    crypto.getRandomValues(randomBytes);
    const jobId = Array.from(randomBytes).map((b) => b.toString(16).padStart(2, '0')).join('');

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunkBlob = file.slice(start, end);

      const fd = new FormData();
      fd.append('chunk', chunkBlob, 'chunk.bin');
      fd.append('job_id', jobId);
      fd.append('chunk_index', i);
      fd.append('total_chunks', totalChunks);
      fd.append('filename', file.name);

      if (onProgress) {
        const pct = Math.round(((i + 1) / totalChunks) * 100);
        onProgress(pct, i + 1, totalChunks);
      }

      const res = await fetch('/upload-chunk', { method: 'POST', body: fd });
      await safeJson(res);
    }

    return jobId;
  }

  async function handleLoadPages() {
    splitBtn.disabled = true;
    pagePicker.classList.add('hidden');
    resultsSection.style.display = 'none';

    try {
      let data;
      if (pendingFile) {
        showStatus([`Preparing ${Math.round(pendingFile.size / (1024 * 1024))} MB upload…`]);
        const jobId = await uploadFileInChunks(pendingFile, (pct, cur, total) => {
          showStatus([`Uploading ${Math.round(pendingFile.size / (1024 * 1024))} MB… (${pct}%)`]);
        });
        showStatus(['Reading the PDF…', 'Rendering page previews…', 'Almost there…']);
        const res = await fetch('/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ job_id: jobId }),
        });
        data = await safeJson(res);
      } else {
        showStatus(['Fetching the PDF…', 'Rendering page previews…', 'Almost there…']);
        const res = await fetch('/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pdf_url: urlInput.value.trim() }),
        });
        data = await safeJson(res);
      }
      buildPageGrid(data.job_id, data.page_count);
    } catch (err) {
      showError(err.message || String(err));
      showEmpty();
    } finally {
      hideStatus();
      splitBtn.disabled = false;
    }
  }

  function buildPageGrid(jobId, totalPages) {
    pagePickerJobId = jobId;
    pagePickerTotal = totalPages;
    pickerKind = mode === 'sections' ? 'sections' : 'pages';
    pagePicker.classList.toggle('sections-mode', pickerKind === 'sections');
    pickerTitle.textContent = pickerKind === 'sections' ? 'Create sections' : 'Select pages';
    // "Select pages" starts with everything selected; "Manual sections" starts empty
    selectedPages = pickerKind === 'sections'
      ? new Set()
      : new Set(Array.from({ length: totalPages }, (_, i) => i + 1));
    sectionsData = [];
    sectionCrops = {};
    lastClickedPage = null;
    sectionName.value = '';
    sectionPages.value = '';
    setSectionMsg('', false);
    reopenSectionsBtn.classList.add('hidden');
    pageGrid.innerHTML = '';

    for (let n = 1; n <= totalPages; n++) {
      const thumb = document.createElement('div');
      thumb.className = 'page-thumb' + (selectedPages.has(n) ? ' selected' : '');
      thumb.dataset.page = n;
      thumb.innerHTML = `
        <img src="/thumbnail/${jobId}/${n}" alt="Page ${n}" loading="lazy" onerror="if(!this.dataset.retried){this.dataset.retried='1';setTimeout(()=>this.src=this.src,1000);}" />
        <div class="page-thumb-check">\u2713</div>
        <button type="button" class="page-thumb-crop" title="Crop this page \u2014 keep only the part you need">\u2702</button>
        <div class="page-thumb-num">${n}</div>
      `;
      thumb.querySelector('img').addEventListener('load', () => paintCrops());
      thumb.querySelector('.page-thumb-crop').addEventListener('click', (e) => {
        e.stopPropagation();                       // do not select/deselect the page
        openCropModal(n);
      });
      thumb.addEventListener('click', (e) => {
        if (pickerKind === 'sections' && e.shiftKey && lastClickedPage) {
          // shift-click selects the whole run between the last click and this one
          const lo = Math.min(lastClickedPage, n), hi = Math.max(lastClickedPage, n);
          for (let p = lo; p <= hi; p++) selectedPages.add(p);
          paintSelection();
        } else if (selectedPages.has(n)) {
          selectedPages.delete(n);
          thumb.classList.remove('selected');
        } else {
          selectedPages.add(n);
          thumb.classList.add('selected');
        }
        lastClickedPage = n;
        updatePagePickerCount();
      });
      pageGrid.appendChild(thumb);
    }

    updatePagePickerCount();
    if (pickerKind === 'sections') renderSections();
    pagePicker.classList.remove('hidden');
    hideEmpty();
  }

  function updatePagePickerCount() {
    const n = selectedPages.size;
    if (pickerKind === 'sections') {
      sectionSelCount.textContent = n ? `${n} page${n === 1 ? '' : 's'} selected` : 'Click pages (shift-click for a run) or type a range';
      if (document.activeElement !== sectionPages) sectionPages.value = pagesLabel(selectedPages);
      return;
    }
    if (combinePages) {
      pagePickerCount.textContent = `${n} of ${pagePickerTotal} pages selected \u2192 1 PDF will be created`;
    } else {
      pagePickerCount.textContent = `${n} of ${pagePickerTotal} pages selected \u2192 ${n} PDF${n === 1 ? '' : 's'} will be created`;
    }
  }

  selectAllBtn.addEventListener('click', () => {
    selectedPages = new Set(Array.from({ length: pagePickerTotal }, (_, i) => i + 1));
    [...pageGrid.querySelectorAll('.page-thumb')].forEach((t) => t.classList.add('selected'));
    updatePagePickerCount();
  });

  deselectAllBtn.addEventListener('click', () => {
    selectedPages.clear();
    [...pageGrid.querySelectorAll('.page-thumb')].forEach((t) => t.classList.remove('selected'));
    updatePagePickerCount();
  });

  extractModeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-combine]');
    if (!btn) return;
    combinePages = btn.dataset.combine === 'true';
    [...extractModeToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
    updatePagePickerCount();
  });

  confirmPagesBtn.addEventListener('click', async () => {
    if (!selectedPages.size) {
      showError('Select at least one page.');
      return;
    }
    hideError();
    confirmPagesBtn.disabled = true;
    showStatus(['Cutting the pages you picked…', 'Almost there…']);
    try {
      const res = await fetch('/split-pages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          job_id: pagePickerJobId,
          pages: [...selectedPages].sort((a, b) => a - b),
          combine: combinePages,
        }),
      });
      await handleJobResponse(res);
      pagePicker.classList.add('hidden');
    } catch (err) {
      showError(err.message || String(err));
    } finally {
      hideStatus();
      confirmPagesBtn.disabled = false;
    }
  });


  // ======================================================= MANUAL SECTIONS ===
  // "Manual sections" mode: pick pages, type the lesson name, press Create - repeat.
  // Each section is saved on the server straight away; download one by one or all
  // together whenever needed.

  const pickerTitle = document.getElementById('pickerTitle');
  const sectionName = document.getElementById('sectionName');
  const sectionPages = document.getElementById('sectionPages');
  const sectionSelCount = document.getElementById('sectionSelCount');
  const createSectionBtn = document.getElementById('createSectionBtn');
  const sectionMsg = document.getElementById('sectionMsg');
  const sectionsList = document.getElementById('sectionsList');
  const sectionsDownloadAll = document.getElementById('sectionsDownloadAll');
  const sectionsDoneBtn = document.getElementById('sectionsDoneBtn');
  const reopenSectionsBtn = document.getElementById('reopenSectionsBtn');

  function pagesLabel(set) {
    const pages = [...set].sort((a, b) => a - b);
    if (!pages.length) return '';
    const runs = [];
    let start = pages[0], prev = pages[0];
    for (const p of pages.slice(1)) {
      if (p === prev + 1) { prev = p; continue; }
      runs.push([start, prev]);
      start = prev = p;
    }
    runs.push([start, prev]);
    return runs.map(([a, b]) => (a === b ? `${a}` : `${a}-${b}`)).join(', ');
  }

  function parsePageText(text, total) {
    const out = new Set();
    for (const part of text.split(',')) {
      const m = part.trim().match(/^(\d+)\s*(?:[-\u2013]\s*(\d+|end))?$/i);
      if (!m) continue;
      let a = parseInt(m[1], 10);
      let b = m[2] === undefined ? a : (m[2].toLowerCase() === 'end' ? total : parseInt(m[2], 10));
      if (a > b) [a, b] = [b, a];
      for (let n = Math.max(a, 1); n <= Math.min(b, total); n++) out.add(n);
    }
    return out;
  }

  function paintSelection() {
    [...pageGrid.querySelectorAll('.page-thumb')].forEach((t) => {
      t.classList.toggle('selected', selectedPages.has(Number(t.dataset.page)));
    });
  }

  function paintUsed() {
    [...pageGrid.querySelectorAll('.page-thumb-used')].forEach((b) => b.remove());
    const badge = {};
    sectionsData.forEach((sec) => (sec.page_list || []).forEach((p) => { badge[p] = sec.id; }));
    [...pageGrid.querySelectorAll('.page-thumb')].forEach((t) => {
      const id = badge[Number(t.dataset.page)];
      if (id === undefined) return;
      const el = document.createElement('div');
      el.className = 'page-thumb-used';
      el.textContent = `#${id}`;
      t.appendChild(el);
    });
  }

  function setSectionMsg(text, isError) {
    sectionMsg.textContent = text;
    sectionMsg.classList.toggle('error', !!isError);
  }

  async function sectionsApi(url, payload) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return await safeJson(res);
  }

  function renderSections() {
    sectionsList.innerHTML = '';
    if (!sectionsData.length) {
      const empty = document.createElement('li');
      empty.className = 'sec-empty';
      empty.textContent = 'Nothing yet \u2014 pick pages, name the lesson, press Create.';
      sectionsList.appendChild(empty);
    }
    sectionsData.forEach((sec) => {
      const li = document.createElement('li');
      li.className = 'sec-item';

      const num = document.createElement('div');
      num.className = 'sec-num';
      num.textContent = String(sec.id).padStart(2, '0');

      const body = document.createElement('div');
      body.className = 'sec-body';
      const title = document.createElement('div');
      title.className = 'sec-title';
      title.textContent = sec.title;
      title.title = sec.title;
      const meta = document.createElement('div');
      meta.className = 'sec-meta';
      meta.textContent = `pages ${sec.pages}` + (sec.cropped && sec.cropped.length ? ` \u00b7 \u2702 cropped: ${sec.cropped.join(', ')}` : '');
      body.append(title, meta);

      const dl = document.createElement('a');
      dl.className = 'sec-act';
      dl.href = sec.download_url;
      dl.title = 'Download this section';
      dl.textContent = '\u2193';

      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'sec-act sec-del';
      del.title = 'Remove this section';
      del.textContent = '\u00d7';
      del.addEventListener('click', async () => {
        if (!confirm(`Remove "${sec.title}"?`)) return;
        try {
          const data = await sectionsApi('/delete-section', { job_id: pagePickerJobId, id: sec.id });
          sectionsData = data.sections;
          renderSections();
          paintUsed();
        } catch (err) {
          setSectionMsg(err.message, true);
        }
      });

      li.append(num, body, dl, del);
      sectionsList.appendChild(li);
    });
    sectionsDownloadAll.href = `/download-all/${pagePickerJobId}`;
    sectionsDownloadAll.classList.toggle('disabled', !sectionsData.length);
    sectionsList.scrollTop = sectionsList.scrollHeight;
  }

  sectionPages.addEventListener('input', () => {
    selectedPages = parsePageText(sectionPages.value, pagePickerTotal);
    paintSelection();
    updatePagePickerCount();
  });

  [sectionName, sectionPages].forEach((el) =>
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); createSectionBtn.click(); }
    })
  );

  createSectionBtn.addEventListener('click', async () => {
    setSectionMsg('', false);
    if (!selectedPages.size) {
      setSectionMsg('Pick at least one page first.', true);
      return;
    }
    createSectionBtn.disabled = true;
    try {
      const data = await sectionsApi('/create-section', {
        job_id: pagePickerJobId,
        name: sectionName.value,
        pages: [...selectedPages].sort((a, b) => a - b),
        crops: Object.fromEntries(Object.entries(sectionCrops).filter(([p]) => selectedPages.has(Number(p)))),
      });
      sectionsData = data.sections;
      const made = sectionsData[sectionsData.length - 1];
      sectionName.value = '';
      selectedPages = new Set();
      sectionCrops = {};
      lastClickedPage = null;
      paintSelection();
      paintCrops();
      sectionPages.value = '';
      updatePagePickerCount();
      renderSections();
      paintUsed();
      setSectionMsg(`Created \u201c${made.title}\u201d (pages ${made.pages}).`, false);
      sectionName.focus();
    } catch (err) {
      setSectionMsg(err.message, true);
    } finally {
      createSectionBtn.disabled = false;
    }
  });


  // ============================================================ CROP A PAGE ===
  // For a lesson that ends (or starts) half-way down a page: pick the part of that page to keep.
  const cropModal = document.getElementById('cropModal');
  const cropStage = document.getElementById('cropStage');
  const cropImg = document.getElementById('cropImg');
  const cropRectEl = document.getElementById('cropRect');
  const cropTitle = document.getElementById('cropTitle');
  const cropInfo = document.getElementById('cropInfo');
  const FULL = [0, 0, 1, 1];
  const MIN_CROP = 0.04;
  let cropPage = null;
  let cropBox = [...FULL];         // [x0, y0, x1, y1] fractions of the page, top-left origin
  let cropDrag = null;

  const clamp01 = (v) => Math.min(1, Math.max(0, v));
  const isFull = (b) => b[0] <= 0.005 && b[1] <= 0.005 && b[2] >= 0.995 && b[3] >= 0.995;

  function renderCropRect() {
    const [x0, y0, x1, y1] = cropBox;
    cropRectEl.style.left = `${x0 * 100}%`;
    cropRectEl.style.top = `${y0 * 100}%`;
    cropRectEl.style.width = `${(x1 - x0) * 100}%`;
    cropRectEl.style.height = `${(y1 - y0) * 100}%`;
    cropInfo.textContent = isFull(cropBox)
      ? 'Whole page. Drag on the page to choose the part to keep.'
      : `Keeping ${Math.round((x1 - x0) * 100)}% of the width, from ${Math.round(y0 * 100)}% to ${Math.round(y1 * 100)}% of the height (from the top).`;
  }

  function openCropModal(page) {
    cropPage = page;
    cropBox = sectionCrops[page] ? [...sectionCrops[page]] : [...FULL];
    cropTitle.textContent = `Crop page ${page}`;
    cropImg.src = `/page-preview/${pagePickerJobId}/${page}?mode=full`;
    renderCropRect();
    cropModal.classList.remove('hidden');
  }
  const closeCropModal = () => cropModal.classList.add('hidden');

  function stagePoint(e) {
    const r = cropImg.getBoundingClientRect();
    return { x: clamp01((e.clientX - r.left) / r.width), y: clamp01((e.clientY - r.top) / r.height) };
  }

  cropStage.addEventListener('pointerdown', (e) => {
    const p = stagePoint(e);
    const h = e.target.dataset ? e.target.dataset.h : undefined;
    if (h) {
      cropDrag = { type: h, orig: [...cropBox], start: p };
    } else if (e.target === cropRectEl && !isFull(cropBox)) {
      cropDrag = { type: 'move', orig: [...cropBox], start: p };
    } else {
      cropDrag = { type: 'new', start: p };
      cropBox = [p.x, p.y, p.x, p.y];
    }
    cropStage.setPointerCapture(e.pointerId);
    renderCropRect();
    e.preventDefault();
  });

  cropStage.addEventListener('pointermove', (e) => {
    if (!cropDrag) return;
    const p = stagePoint(e);
    const d = cropDrag;
    if (d.type === 'new') {
      cropBox = [Math.min(d.start.x, p.x), Math.min(d.start.y, p.y), Math.max(d.start.x, p.x), Math.max(d.start.y, p.y)];
    } else if (d.type === 'move') {
      const w = d.orig[2] - d.orig[0], hgt = d.orig[3] - d.orig[1];
      const x0 = Math.min(1 - w, Math.max(0, d.orig[0] + p.x - d.start.x));
      const y0 = Math.min(1 - hgt, Math.max(0, d.orig[1] + p.y - d.start.y));
      cropBox = [x0, y0, x0 + w, y0 + hgt];
    } else {
      let [x0, y0, x1, y1] = d.orig;
      if (d.type.includes('w')) x0 = Math.min(p.x, x1 - MIN_CROP);
      if (d.type.includes('e')) x1 = Math.max(p.x, x0 + MIN_CROP);
      if (d.type.includes('n')) y0 = Math.min(p.y, y1 - MIN_CROP);
      if (d.type.includes('s')) y1 = Math.max(p.y, y0 + MIN_CROP);
      cropBox = [x0, y0, x1, y1];
    }
    renderCropRect();
  });

  const endCropDrag = () => {
    if (!cropDrag) return;
    cropDrag = null;
    const [x0, y0, x1, y1] = cropBox;
    if (x1 - x0 < MIN_CROP || y1 - y0 < MIN_CROP) cropBox = [...FULL];   // a click, not a drag
    renderCropRect();
  };
  cropStage.addEventListener('pointerup', endCropDrag);
  cropStage.addEventListener('pointercancel', endCropDrag);

  document.querySelectorAll('.crop-presets button').forEach((btn) =>
    btn.addEventListener('click', () => {
      cropBox = {
        top: [0, 0, 1, 0.5], bottom: [0, 0.5, 1, 1], left: [0, 0, 0.5, 1], right: [0.5, 0, 1, 1], full: [...FULL],
      }[btn.dataset.preset];
      renderCropRect();
    })
  );

  document.getElementById('cropApply').addEventListener('click', () => {
    if (isFull(cropBox)) delete sectionCrops[cropPage]; else sectionCrops[cropPage] = cropBox.map((v) => Math.round(v * 10000) / 10000);
    if (sectionCrops[cropPage]) selectedPages.add(cropPage);        // cropping a page means you want it
    paintSelection();
    updatePagePickerCount();
    paintCrops();
    closeCropModal();
  });
  document.getElementById('cropReset').addEventListener('click', () => {
    delete sectionCrops[cropPage];
    paintCrops();
    closeCropModal();
  });
  document.getElementById('cropClose').addEventListener('click', closeCropModal);
  cropModal.addEventListener('click', (e) => { if (e.target === cropModal) closeCropModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCropModal(); });

  // shows the kept part on the thumbnail (dashed frame, the rest dimmed) and marks the button
  function paintCrops() {
    [...pageGrid.querySelectorAll('.page-thumb')].forEach((t) => {
      const n = Number(t.dataset.page);
      const old = t.querySelector('.page-thumb-cropbox');
      if (old) old.remove();
      const btn = t.querySelector('.page-thumb-crop');
      const c = sectionCrops[n];
      if (btn) btn.classList.toggle('on', !!c);
      const img = t.querySelector('img');
      if (!c || !img || !img.naturalWidth) return;
      // the thumbnail is letter-boxed inside its frame (object-fit: contain)
      const fw = t.clientWidth, fh = t.clientHeight;
      const scale = Math.min(fw / img.naturalWidth, fh / img.naturalHeight);
      const dw = img.naturalWidth * scale, dh = img.naturalHeight * scale;
      const ox = (fw - dw) / 2, oy = (fh - dh) / 2;
      const box = document.createElement('div');
      box.className = 'page-thumb-cropbox';
      box.style.left = `${ox + c[0] * dw}px`;
      box.style.top = `${oy + c[1] * dh}px`;
      box.style.width = `${(c[2] - c[0]) * dw}px`;
      box.style.height = `${(c[3] - c[1]) * dh}px`;
      t.appendChild(box);
    });
  }

  function showSectionsAsResults() {
    const n = sectionsData.length;
    resultsCount.textContent = `${n} section${n === 1 ? '' : 's'} \u00b7 job ${pagePickerJobId}`;
    jobTag.textContent = `job ${pagePickerJobId}`;
    downloadAllBtn.href = `/download-all/${pagePickerJobId}`;
    downloadAllBtn.style.display = n > 1 ? 'inline-flex' : 'none';
    renderCards(sectionsData.map((s) => ({ ...s })));
    resultsNotice.textContent = '';
    reopenSectionsBtn.classList.remove('hidden');
    resultsSection.style.display = 'block';
  }

  function closePicker() {
    pagePicker.classList.add('hidden');
    if (pickerKind === 'sections' && sectionsData.length) showSectionsAsResults();
  }

  closePagePickerBtn.addEventListener('click', closePicker);
  sectionsDoneBtn.addEventListener('click', closePicker);
  reopenSectionsBtn.addEventListener('click', () => pagePicker.classList.remove('hidden'));


  // ===================================================== SPELL-CHECK RESULTS ===
  // Nepali Spell Check mode shows its findings right in the portal: unit, lesson,
  // page, line, the wrong word, what it should probably be, and the sentence around it.

  const spellPanel = document.getElementById('spellPanel');
  const spellSummary = document.getElementById('spellSummary');
  const spellSearch = document.getElementById('spellSearch');
  const spellUnitFilter = document.getElementById('spellUnitFilter');
  const spellCsv = document.getElementById('spellCsv');
  const spellFilesToggle = document.getElementById('spellFilesToggle');
  const spellList = document.getElementById('spellList');
  const spellMoreBtn = document.getElementById('spellMoreBtn');
  const spellModal = document.getElementById('spellModal');
  const spellModalImg = document.getElementById('spellModalImg');
  const spellModalTitle = document.getElementById('spellModalTitle');
  const spellModalFull = document.getElementById('spellModalFull');
  const spellModalClose = document.getElementById('spellModalClose');
  let spellModalItem = null;
  let spellModalMode = 'band';
  const spellTabLikely = document.getElementById('spellTabLikely');
  const spellTabOther = document.getElementById('spellTabOther');
  const spellHint = document.getElementById('spellHint');
  let spellTab = 'likely';

  const SPELL_PAGE_SIZE = 400;
  let spellData = null;
  let currentJobId = '';
  let spellLimit = SPELL_PAGE_SIZE;

  function spellGroupKey(it) { return it.unit || it.lesson || 'Unit not detected'; }

  function statBox(value, label, kind) {
    const box = document.createElement('div');
    box.className = `spell-stat ${kind || ''}`;
    const b = document.createElement('b');
    b.textContent = value;
    const s = document.createElement('span');
    s.textContent = label;
    box.append(b, s);
    return box;
  }

  function showSpellcheck(data) {
    spellData = data.spellcheck;
    currentJobId = data.job_id;
    spellLimit = SPELL_PAGE_SIZE;
    spellSearch.value = '';

    spellTab = spellData.items.some((it) => it.suggestions.length) ? 'likely' : 'other';
    refreshSpellMeta();

    const csv = (data.chapters || []).find((c) => c.filename === 'Spellcheck_Report.csv');
    spellCsv.href = csv ? csv.download_url : '#';
    spellCsv.style.display = csv ? '' : 'none';

    cardsList.style.display = 'none';
    spellFilesToggle.textContent = 'Show extracted files';
    resultsTitle.textContent = 'Spell check results';
    spellPanel.style.display = 'block';
    renderSpellList();
  }

  function refreshSpellMeta() {
    const items = spellData.items;
    const likely = items.filter((it) => it.suggestions.length);
    const words = (list) => new Set(list.map((it) => it.word)).size;
    const total = Number(spellData.words_checked);

    spellSummary.innerHTML = '';
    spellSummary.append(
      statBox(likely.length, 'likely mistakes', likely.length ? 'bad' : 'good'),
      statBox(items.length - likely.length, 'names / unknown words'),
      statBox(total, 'words checked'),
    );
    spellTabLikely.textContent = `Likely mistakes (${likely.length})`;
    spellTabOther.textContent = `Names / unknown words (${items.length - likely.length})`;
    spellTabLikely.classList.toggle('active', spellTab === 'likely');
    spellTabOther.classList.toggle('active', spellTab === 'other');
    spellHint.textContent = spellTab === 'likely'
      ? 'Words that are not in the dictionary but look like a slip of a real word, with the probable correct word. Names and new words can appear here too \u2014 press \u201cNot a mistake\u201d and they will never be flagged again.'
      : 'Words not in the dictionary with nothing similar \u2014 mostly names, places and foreign words. Press \u201cNot a mistake\u201d to add a word to the dictionary.';

    // unit filter, in book order, with counts for the current tab
    const keep = spellUnitFilter.value;
    const counts = new Map();
    items.filter((it) => (spellTab === 'likely') === (it.suggestions.length > 0))
      .forEach((it) => counts.set(spellGroupKey(it), (counts.get(spellGroupKey(it)) || 0) + 1));
    spellUnitFilter.innerHTML = '';
    const all = document.createElement('option');
    all.value = '';
    all.textContent = 'All units';
    spellUnitFilter.appendChild(all);
    counts.forEach((n, key) => {
      const o = document.createElement('option');
      o.value = key;
      o.textContent = `${key} (${n})`;
      spellUnitFilter.appendChild(o);
    });
    if ([...counts.keys()].includes(keep)) spellUnitFilter.value = keep;
    spellUnitFilter.style.display = counts.size > 1 ? '' : 'none';
    resultsCount.textContent = `${likely.length} likely mistake${likely.length === 1 ? '' : 's'} \u00b7 ${items.length - likely.length} names/unknown \u00b7 job ${currentJobId}`;
  }

  function spellPreviewUrl(it, mode) {
    const [x0, y0, x1, y1] = it.box;
    const [w0, w1] = it.wx || [-1, -1];
    return `/page-preview/${currentJobId}/${it.page}?x0=${x0}&y0=${y0}&x1=${x1}&y1=${y1}&wx0=${w0}&wx1=${w1}&mode=${mode}&t=${Date.now()}`;
  }

  function openSpellModal(it) {
    spellModalItem = it;
    spellModalMode = 'band';
    spellModalTitle.textContent = `${it.word}  \u2014  page ${it.page}, line ${it.line}`;
    spellModalFull.textContent = 'Show full page';
    spellModalImg.src = spellPreviewUrl(it, 'band');
    spellModal.classList.remove('hidden');
  }

  spellModalFull.addEventListener('click', () => {
    spellModalMode = spellModalMode === 'band' ? 'full' : 'band';
    spellModalFull.textContent = spellModalMode === 'band' ? 'Show full page' : 'Show only this line';
    spellModalImg.src = spellPreviewUrl(spellModalItem, spellModalMode);
  });
  spellModalClose.addEventListener('click', () => spellModal.classList.add('hidden'));
  spellModal.addEventListener('click', (e) => { if (e.target === spellModal) spellModal.classList.add('hidden'); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') spellModal.classList.add('hidden'); });

  function dismissWord(word) {
    spellData.items = spellData.items.filter((it) => it.word !== word);
    refreshSpellMeta();
    renderSpellList();
  }

  function hideSpellcheck() {
    spellPanel.style.display = 'none';
    cardsList.style.display = '';
    resultsTitle.textContent = activeTool === 'convert' ? 'Converted' : 'Files ready';
    spellData = null;
  }

  function renderSpellList() {
    spellList.innerHTML = '';
    const q = spellSearch.value.trim();
    const unit = spellUnitFilter.value;
    const items = spellData.items.filter((it) =>
      (!unit || spellGroupKey(it) === unit) &&
      ((spellTab === 'likely') === (it.suggestions.length > 0)) &&
      (!q || it.word.includes(q) || it.suggestions.some((s) => s.includes(q)))
    );

    if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'spell-empty';
      empty.textContent = spellData.items.length
        ? 'Nothing here with these filters.'
        : 'No spelling mistakes found \u2014 every word is in the dictionary.';
      spellList.appendChild(empty);
      spellMoreBtn.classList.add('hidden');
      return;
    }

    const shown = items.slice(0, spellLimit);
    let lastKey = null;
    let headCount = null;
    shown.forEach((it) => {
      const key = `${spellGroupKey(it)}\u0000${it.lesson || ''}`;
      if (key !== lastKey) {
        lastKey = key;
        const head = document.createElement('div');
        head.className = 'spell-group-head';
        const u = document.createElement('span');
        u.className = 'spell-unit';
        u.textContent = it.unit || (it.lesson ? it.lesson : 'Unit not detected');
        head.appendChild(u);
        if (it.unit && it.lesson) {
          const l = document.createElement('span');
          l.className = 'spell-lesson';
          l.textContent = it.lesson;
          head.appendChild(l);
        }
        headCount = document.createElement('span');
        headCount.className = 'spell-count';
        headCount.dataset.n = '0';
        head.appendChild(headCount);
        spellList.appendChild(head);
      }
      headCount.dataset.n = String(Number(headCount.dataset.n) + 1);
      headCount.textContent = `${headCount.dataset.n} mistake${headCount.dataset.n === '1' ? '' : 's'}`;

      const row = document.createElement('div');
      row.className = 'spell-row';

      const loc = document.createElement('div');
      loc.className = 'spell-loc';
      const pg = document.createElement('span');
      pg.className = 'spell-page';
      pg.textContent = `Page ${it.page}`;
      const ln = document.createElement('span');
      ln.textContent = `line ${it.line} from top`;
      loc.append(pg);
      if (it.printed) {
        const pr = document.createElement('span');
        pr.className = 'spell-printed';
        pr.textContent = `printed p. ${it.printed}`;
        loc.append(pr);
      }
      loc.append(ln);

      const fix = document.createElement('div');
      fix.className = 'spell-fix';
      const wrong = document.createElement('span');
      wrong.className = 'spell-wrong';
      wrong.textContent = it.word;
      fix.appendChild(wrong);
      if (it.suggestions.length) {
        const arrow = document.createElement('span');
        arrow.className = 'spell-arrow';
        arrow.textContent = '\u2192 should be';
        fix.appendChild(arrow);
        it.suggestions.forEach((s) => {
          const r = document.createElement('span');
          r.className = 'spell-right';
          r.textContent = s;
          fix.appendChild(r);
        });
      } else {
        const none = document.createElement('span');
        none.className = 'spell-none';
        none.textContent = 'no suggestion (maybe a name)';
        fix.appendChild(none);
      }

      const ctx = document.createElement('div');
      ctx.className = 'spell-ctx';
      const mark = document.createElement('mark');
      mark.textContent = it.word;
      ctx.append(document.createTextNode(it.before), mark, document.createTextNode(it.after));

      row.append(loc, fix, ctx);
      if (it.box) {
        const view = document.createElement('button');
        view.type = 'button';
        view.className = 'spell-view';
        view.textContent = 'View on page';
        view.title = 'Show this line on the PDF page';
        view.addEventListener('click', () => openSpellModal(it));
        fix.appendChild(view);
      }

      const ok = document.createElement('button');
      ok.type = 'button';
      ok.className = 'spell-ok';
      ok.textContent = '\u2713 Not a mistake';
      ok.title = `Add \u201c${it.word}\u201d to the dictionary \u2014 it will not be flagged again`;
      ok.addEventListener('click', async () => {
        ok.disabled = true;
        try {
          await fetch('/dictionary/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ word: it.word }),
          });
        } catch (err) { /* still hide it for this session */ }
        dismissWord(it.word);
      });
      fix.appendChild(ok);

      spellList.appendChild(row);
    });

    const left = items.length - shown.length;
    spellMoreBtn.classList.toggle('hidden', left <= 0);
    spellMoreBtn.textContent = `Show ${Math.min(left, SPELL_PAGE_SIZE)} more (${left} left)`;
  }

  spellSearch.addEventListener('input', () => { spellLimit = SPELL_PAGE_SIZE; renderSpellList(); });
  [[spellTabLikely, 'likely'], [spellTabOther, 'other']].forEach(([btn, which]) =>
    btn.addEventListener('click', () => {
      spellTab = which;
      spellLimit = SPELL_PAGE_SIZE;
      refreshSpellMeta();
      renderSpellList();
    })
  );
  spellUnitFilter.addEventListener('change', () => { spellLimit = SPELL_PAGE_SIZE; renderSpellList(); });
  spellMoreBtn.addEventListener('click', () => { spellLimit += SPELL_PAGE_SIZE; renderSpellList(); });
  spellFilesToggle.addEventListener('click', () => {
    const show = cardsList.style.display === 'none';
    cardsList.style.display = show ? '' : 'none';
    spellFilesToggle.textContent = show ? 'Hide extracted files' : 'Show extracted files';
  });

  splitForm.addEventListener('submit', handleSplitSubmit);

  // ============================================================ MERGE ===

  const pickMergeBtn = document.getElementById('pickMergeBtn');
  const pickMergeLabel = document.getElementById('pickMergeLabel');
  const mergeFilesInput = document.getElementById('mergeFilesInput');
  const mergeList = document.getElementById('mergeList');
  const mergeBtn = document.getElementById('mergeBtn');

  let mergeFiles = [];

  pickMergeBtn.addEventListener('click', () => mergeFilesInput.click());

  mergeFilesInput.addEventListener('change', () => {
    mergeFiles = mergeFiles.concat(Array.from(mergeFilesInput.files || []));
    mergeFilesInput.value = '';
    renderMergeList();
  });

  function renderMergeList() {
    mergeList.innerHTML = '';
    pickMergeLabel.textContent = mergeFiles.length
      ? `${mergeFiles.length} PDF${mergeFiles.length === 1 ? '' : 's'} chosen — add more?`
      : 'Choose 2 or more PDFs (in order)…';

    mergeFiles.forEach((f, idx) => {
      const item = document.createElement('div');
      item.className = 'merge-item';
      item.draggable = true;
      item.dataset.idx = idx;
      item.innerHTML = `
        <span class="handle">\u2630</span>
        <span class="merge-num">${idx + 1}</span>
        <span class="merge-name">${escapeHtml(f.name)}</span>
        <button type="button" class="merge-remove" data-idx="${idx}" aria-label="Remove">\u00d7</button>
      `;
      mergeList.appendChild(item);
    });
  }

  mergeList.addEventListener('click', (e) => {
    const btn = e.target.closest('.merge-remove');
    if (!btn) return;
    mergeFiles.splice(parseInt(btn.dataset.idx, 10), 1);
    renderMergeList();
  });

  let dragSrcIdx = null;
  mergeList.addEventListener('dragstart', (e) => {
    const item = e.target.closest('.merge-item');
    if (!item) return;
    dragSrcIdx = parseInt(item.dataset.idx, 10);
    item.classList.add('dragging');
  });
  mergeList.addEventListener('dragend', (e) => {
    const item = e.target.closest('.merge-item');
    if (item) item.classList.remove('dragging');
  });
  mergeList.addEventListener('dragover', (e) => {
    e.preventDefault();
  });
  mergeList.addEventListener('drop', (e) => {
    e.preventDefault();
    const target = e.target.closest('.merge-item');
    if (!target || dragSrcIdx === null) return;
    const targetIdx = parseInt(target.dataset.idx, 10);
    if (targetIdx === dragSrcIdx) return;
    const [moved] = mergeFiles.splice(dragSrcIdx, 1);
    mergeFiles.splice(targetIdx, 0, moved);
    dragSrcIdx = null;
    renderMergeList();
  });

  async function handleMergeSubmit(e) {
    e.preventDefault();
    hideError();
    hideEmpty();
    resultsSection.style.display = 'none';

    if (mergeFiles.length < 2) {
      showError('Choose at least 2 PDFs to merge.');
      return;
    }

    mergeBtn.disabled = true;
    showStatus(['Reading your PDFs…', 'Stitching them together in order…', 'Almost there…']);

    try {
      const fd = new FormData();
      mergeFiles.forEach((f) => fd.append('files', f));
      const res = await fetch('/merge', { method: 'POST', body: fd });
      await handleJobResponse(res);
    } catch (err) {
      showError(err.message || String(err));
      showEmpty();
    } finally {
      hideStatus();
      mergeBtn.disabled = false;
    }
  }

  mergeForm.addEventListener('submit', handleMergeSubmit);

  // ========================================================== CONVERT ===

  const convertUrl = document.getElementById('convertUrl');
  const convertBtn = document.getElementById('convertBtn');
  const convertBtnMulti = document.getElementById('convertBtnMulti');
  const convertTypeToggle = document.getElementById('convertTypeToggle');
  const convertSingleRow = document.getElementById('convertSingleRow');
  const convertMultiRow = document.getElementById('convertMultiRow');
  const pickImagesBtn = document.getElementById('pickImagesBtn');
  const pickImagesLabel = document.getElementById('pickImagesLabel');
  const imagesInput = document.getElementById('imagesInput');
  const imgFormatWrap = document.getElementById('imgFormatWrap');
  const imgFormatToggle = document.getElementById('imgFormatToggle');
  const convertUploadAltBtn = document.getElementById('convertUploadAltBtn');
  const convertFileInput = document.getElementById('convertFileInput');
  const wordToPdfNote = document.getElementById('wordToPdfNote');

  let convType = 'pdf_to_word';
  let imgFormat = 'jpg';
  let convertPendingFile = null;
  let pendingImages = [];

  const CONVERT_ACCEPT = {
    pdf_to_word: 'application/pdf',
    pdf_to_image: 'application/pdf',
    word_to_pdf: '.doc,.docx',
    image_to_pdf: 'image/*',
  };

  const CONVERT_STATUS = {
    pdf_to_word: ['Reading the PDF…', 'Rebuilding it as a document…', 'Almost there…'],
    pdf_to_image: ['Reading the PDF…', 'Rendering each page as an image…', 'Almost there…'],
    word_to_pdf: ['Reading the document…', 'Converting to PDF…', 'Almost there…'],
    image_to_pdf: ['Reading your images…', 'Combining into one PDF…', 'Almost there…'],
  };

  function updateConvertUI() {
    const isMulti = convType === 'image_to_pdf';
    convertSingleRow.classList.toggle('hidden', isMulti);
    convertMultiRow.classList.toggle('hidden', !isMulti);
    imgFormatWrap.classList.toggle('hidden', convType !== 'pdf_to_image');
    convertUploadAltBtn.classList.toggle('hidden', isMulti);
    wordToPdfNote.classList.toggle('visible', convType === 'word_to_pdf');
    convertFileInput.setAttribute('accept', CONVERT_ACCEPT[convType] || '*');
    convertUrl.placeholder = convType === 'word_to_pdf'
      ? 'https://your-storage.com/document.docx'
      : 'https://your-storage.com/file.pdf';
  }

  convertTypeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-conv]');
    if (!btn) return;
    convType = btn.dataset.conv;
    [...convertTypeToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
    updateConvertUI();
    hideError();
  });

  imgFormatToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-fmt]');
    if (!btn) return;
    imgFormat = btn.dataset.fmt;
    [...imgFormatToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
  });

  convertUploadAltBtn.addEventListener('click', () => convertFileInput.click());
  convertFileInput.addEventListener('change', () => {
    if (convertFileInput.files && convertFileInput.files[0]) {
      convertPendingFile = convertFileInput.files[0];
      convertUrl.value = '';
      convertUrl.placeholder = `Selected file: ${convertPendingFile.name}`;
    }
  });
  convertUrl.addEventListener('input', () => { convertPendingFile = null; });

  pickImagesBtn.addEventListener('click', () => imagesInput.click());
  imagesInput.addEventListener('change', () => {
    pendingImages = Array.from(imagesInput.files || []);
    pickImagesLabel.textContent = pendingImages.length
      ? `${pendingImages.length} image${pendingImages.length === 1 ? '' : 's'} selected`
      : 'Choose images (in order)…';
  });

  async function handleConvertSubmit(e) {
    e.preventDefault();
    hideError();
    hideEmpty();
    resultsSection.style.display = 'none';

    if (convType === 'image_to_pdf') {
      if (!pendingImages.length) {
        showError('Choose one or more images to combine.');
        return;
      }
    } else if (!convertPendingFile && !convertUrl.value.trim()) {
      showError('Paste a file link, or choose a file to upload.');
      return;
    }

    convertBtn.disabled = true;
    convertBtnMulti.disabled = true;
    showStatus(CONVERT_STATUS[convType]);

    try {
      let res;
      const fd = new FormData();
      fd.append('type', convType);

      if (convType === 'image_to_pdf') {
        pendingImages.forEach((f) => fd.append('files', f));
        res = await fetch('/convert', { method: 'POST', body: fd });
      } else if (convertPendingFile) {
        fd.append('file', convertPendingFile);
        fd.append('format', imgFormat);
        res = await fetch('/convert', { method: 'POST', body: fd });
      } else {
        res = await fetch('/convert', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pdf_url: convertUrl.value.trim(), type: convType, format: imgFormat }),
        });
      }
      await handleJobResponse(res);
    } catch (err) {
      showError(err.message || String(err));
      showEmpty();
    } finally {
      hideStatus();
      convertBtn.disabled = false;
      convertBtnMulti.disabled = false;
    }
  }

  convertForm.addEventListener('submit', handleConvertSubmit);
  updateConvertUI();

  // ========================================================== COMPRESS ===

  const compressUrl = document.getElementById('compressUrl');
  const compressUrlLabel = document.getElementById('compressUrlLabel');
  const compressSelectedFilePill = document.getElementById('compressSelectedFilePill');
  const compressSelectedFileName = document.getElementById('compressSelectedFileName');
  const compressSelectedFileSize = document.getElementById('compressSelectedFileSize');
  const compressRemoveFileBtn = document.getElementById('compressRemoveFileBtn');
  const compressBtn = document.getElementById('compressBtn');
  const compressModeToggle = document.getElementById('compressModeToggle');
  const compressQualityWrap = document.getElementById('compressQualityWrap');
  const compressQualityToggle = document.getElementById('compressQualityToggle');
  const compressUploadAltBtn = document.getElementById('compressUploadAltBtn');
  const compressFileInput = document.getElementById('compressFileInput');

  let compressPendingFile = null;
  let compressMode = 'visually_lossless';
  let compressQuality = 90;

  if (compressModeToggle) {
    compressModeToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-cmode]');
      if (!btn) return;
      compressMode = btn.dataset.cmode;
      [...compressModeToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
      if (compressQualityWrap) {
        compressQualityWrap.classList.toggle('hidden', compressMode === 'lossless');
      }
    });
  }

  if (compressQualityToggle) {
    compressQualityToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-quality]');
      if (!btn) return;
      compressQuality = parseInt(btn.dataset.quality, 10) || 90;
      [...compressQualityToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
    });
  }

  function setCompressPendingFile(file) {
    if (!file) {
      compressPendingFile = null;
      if (compressSelectedFilePill) compressSelectedFilePill.classList.add('hidden');
      if (compressUrlLabel) compressUrlLabel.classList.remove('hidden');
      if (compressUrl) compressUrl.placeholder = "Paste PDF link (or click 'Choose PDF File' below)";
      return;
    }
    compressPendingFile = file;
    if (compressUrl) compressUrl.value = '';
    if (compressSelectedFilePill && compressSelectedFileName && compressSelectedFileSize) {
      compressSelectedFileName.textContent = file.name;
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      compressSelectedFileSize.textContent = file.size > 10 * 1024 * 1024
        ? `${mb} MB • Chunked Stream`
        : `${mb} MB`;
      compressSelectedFilePill.classList.remove('hidden');
      if (compressUrlLabel) compressUrlLabel.classList.add('hidden');
    }
    hideError();
  }

  if (compressRemoveFileBtn) {
    compressRemoveFileBtn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      setCompressPendingFile(null);
      if (compressFileInput) compressFileInput.value = '';
    });
  }

  if (compressUploadAltBtn) {
    compressUploadAltBtn.addEventListener('click', () => {
      if (compressFileInput) compressFileInput.click();
    });
  }

  if (compressFileInput) {
    compressFileInput.addEventListener('change', () => {
      if (compressFileInput.files && compressFileInput.files[0]) {
        setCompressPendingFile(compressFileInput.files[0]);
      }
    });
  }

  if (compressForm) {
    ['dragenter', 'dragover'].forEach((eventName) => {
      compressForm.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        compressForm.classList.add('drag-over');
      });
    });
    ['dragleave', 'drop'].forEach((eventName) => {
      compressForm.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        compressForm.classList.remove('drag-over');
      });
    });
    compressForm.addEventListener('drop', (e) => {
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        const f = e.dataTransfer.files[0];
        if (f.name.toLowerCase().endsWith('.pdf') || f.type === 'application/pdf') {
          setCompressPendingFile(f);
        } else {
          showError('Please drop a valid PDF file.');
        }
      }
    });
  }

  if (compressUrl) {
    compressUrl.addEventListener('input', () => {
      if (compressUrl.value.trim()) {
        setCompressPendingFile(null);
      }
    });
  }

  const COMPRESS_MESSAGES = [
    'Reading PDF streams & structure…',
    'Deduplicating identical image and font objects…',
    'Optimizing raster streams losslessly…',
    'Deflating xref table and rebuilding compact PDF…',
  ];

  async function handleCompressSubmit(e) {
    e.preventDefault();
    hideError();
    hideEmpty();
    resultsSection.style.display = 'none';

    if (!compressPendingFile && (!compressUrl || !compressUrl.value.trim())) {
      if (compressFileInput) compressFileInput.click();
      showError('Please choose a PDF file from your computer or paste a PDF link to compress.');
      return;
    }

    if (compressBtn) compressBtn.disabled = true;
    showStatus(COMPRESS_MESSAGES);

    try {
      let res;
      if (compressPendingFile) {
        if (compressPendingFile.size > 10 * 1024 * 1024) {
          showStatus([`Preparing ${Math.round(compressPendingFile.size / (1024 * 1024))} MB chunked stream…`]);
          const jobId = await uploadFileInChunks(compressPendingFile, (pct) => {
            showStatus([`Streaming upload: ${Math.round(compressPendingFile.size / (1024 * 1024))} MB (${pct}%)`]);
          });
          showStatus(COMPRESS_MESSAGES);
          res = await fetch('/compress', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              job_id: jobId,
              mode: compressMode,
              image_quality: compressQuality,
              filename: compressPendingFile.name,
            }),
          });
        } else {
          showStatus(COMPRESS_MESSAGES);
          const fd = new FormData();
          fd.append('file', compressPendingFile);
          fd.append('mode', compressMode);
          fd.append('image_quality', compressQuality);
          res = await fetch('/compress', {
            method: 'POST',
            body: fd,
          });
        }
      } else {
        res = await fetch('/compress', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            pdf_url: compressUrl.value.trim(),
            mode: compressMode,
            image_quality: compressQuality,
          }),
        });
      }

      await handleJobResponse(res);
    } catch (err) {
      showError(err.message || String(err));
      showEmpty();
    } finally {
      hideStatus();
      if (compressBtn) compressBtn.disabled = false;
    }
  }

  if (compressForm) {
    compressForm.addEventListener('submit', handleCompressSubmit);
  }

  // ======================================================== SHARED UI ===

  function showEmpty() {
    emptyState.classList.add('visible');
    resultsSection.style.display = 'none';
  }

  function hideEmpty() {
    emptyState.classList.remove('visible');
  }

  function showStatus(messages, vars) {
    status.classList.remove('error');
    status.classList.add('visible');
    let i = 0;
    const render = (msg) => Object.entries(vars || {}).reduce((s, [k, v]) => s.replaceAll(`{${k}}`, v), msg);
    statusText.textContent = render(messages[0]);
    status._interval = setInterval(() => {
      i = (i + 1) % messages.length;
      statusText.textContent = render(messages[i]);
    }, 1100);
  }

  function hideStatus() {
    status.classList.remove('visible');
    clearInterval(status._interval);
  }

  function showError(message) {
    errorText.textContent = message;
    errorPanel.classList.add('visible');
  }

  function hideError() {
    errorPanel.classList.remove('visible');
  }

  async function handleJobResponse(res) {
    const data = await safeJson(res);
    resultsCount.textContent = `${data.chapter_count} file${data.chapter_count === 1 ? '' : 's'} · job ${data.job_id}`;
    jobTag.textContent = `job ${data.job_id}`;
    downloadAllBtn.href = data.download_all_url;
    downloadAllBtn.style.display = data.chapter_count > 1 ? 'inline-flex' : 'none';
    renderCards(data.chapters);
    reopenSectionsBtn.classList.add('hidden');
    resultsNotice.textContent = data.notice || '';
    if (data.spellcheck) showSpellcheck(data); else hideSpellcheck();
    resultsSection.style.display = 'block';
  }

  function renderCards(chapters) {
    cardsList.innerHTML = '';
    let lastGroup = undefined;
    let cardIdx = 0;
    let groupIdx = -1;

    chapters.forEach((ch) => {
      if (ch.group !== lastGroup) {
        lastGroup = ch.group;
        groupIdx += 1;
        if (ch.group) {
          const header = document.createElement('div');
          header.className = 'group-header';
          header.style.setProperty('--delay', `${cardIdx * 55}ms`);
          header.textContent = ch.group;
          cardsList.appendChild(header);
        }
      }

      const card = document.createElement('div');
      card.className = 'card';
      const stripe = STRIPES[groupIdx >= 0 ? groupIdx % STRIPES.length : cardIdx % STRIPES.length];
      card.style.setProperty('--stripe', stripe);
      card.style.setProperty('--delay', `${cardIdx * 55}ms`);
      card.style.setProperty('--tilt', `${cardIdx % 2 === 0 ? -0.6 : 0.6}deg`);
      if (ch.group) card.classList.add('card-nested');

      const label = ch.title === 'Front Matter' ? 'FM' : String(cardIdx).padStart(2, '0');

      card.innerHTML = `
        <div class="card-num">${label}</div>
        <div class="card-body">
          <p class="card-title">${escapeHtml(ch.title)}</p>
          <p class="card-meta">PAGES ${ch.pages} · ${escapeHtml(ch.filename)}</p>
        </div>
        <a class="card-dl" href="${ch.download_url}" title="Download ${escapeHtml(ch.title)}" aria-label="Download ${escapeHtml(ch.title)}">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14" />
          </svg>
        </a>
      `;
      cardsList.appendChild(card);
      cardIdx += 1;
    });
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  showEmpty();
})();
