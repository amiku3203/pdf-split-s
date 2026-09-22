(function () {
  // ---------- shared elements ----------
  const toolTabs = document.getElementById('toolTabs');
  const splitForm = document.getElementById('splitForm');
  const mergeForm = document.getElementById('mergeForm');
  const convertForm = document.getElementById('convertForm');

  const status = document.getElementById('status');
  const statusText = document.getElementById('statusText');
  const errorPanel = document.getElementById('errorPanel');
  const errorText = document.getElementById('errorText');
  const emptyState = document.getElementById('emptyState');

  const resultsSection = document.getElementById('resultsSection');
  const resultsTitle = document.getElementById('resultsTitle');
  const resultsCount = document.getElementById('resultsCount');
  const cardsList = document.getElementById('cardsList');
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
    document.getElementById('pagePicker').classList.add('hidden');
    hideError();
    showEmpty();
    resultsTitle.textContent = activeTool === 'convert' ? 'Converted' : 'Files ready';
  });

  // ============================================================ SPLIT ===

  const urlInput = document.getElementById('pdfUrl');
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

  closePagePickerBtn.addEventListener('click', () => {
    pagePicker.classList.add('hidden');
  });

  let pagePickerJobId = null;
  let pagePickerTotal = 0;
  let selectedPages = new Set();
  let combinePages = false;

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
  };

  const MODES_HIDING_KEYWORD = new Set(['lesson', 'range', 'fixed', 'per_page', 'pages']);

  modeSelect.addEventListener('change', () => {
    mode = modeSelect.value;
    kwToggleWrap.classList.toggle('hidden', MODES_HIDING_KEYWORD.has(mode));
    rangeInputWrap.classList.toggle('hidden', mode !== 'range');
    fixedInputWrap.classList.toggle('hidden', mode !== 'fixed');
    splitBtn.innerHTML = mode === 'pages'
      ? '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 4l8 8-8 8" /></svg> Load pages'
      : '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 4l8 8-8 8" /></svg> Split';
    pagePicker.classList.add('hidden');
  });

  kwToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-kw]');
    if (!btn) return;
    keyword = btn.dataset.kw;
    [...kwToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
  });

  uploadAltBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files[0]) {
      pendingFile = fileInput.files[0];
      urlInput.value = '';
      urlInput.placeholder = `Selected file: ${pendingFile.name}`;
    }
  });
  urlInput.addEventListener('input', () => { pendingFile = null; });

  async function handleSplitSubmit(e) {
    e.preventDefault();
    hideError();
    hideEmpty();
    resultsSection.style.display = 'none';

    if (!pendingFile && !urlInput.value.trim()) {
      showError('Paste a PDF link, or choose a file to upload.');
      return;
    }
    if (mode === 'range' && !rangesInput.value.trim()) {
      showError('Enter at least one page range, e.g. "1-5, 6-10".');
      return;
    }

    if (mode === 'pages') {
      await handleLoadPages();
      return;
    }

    splitBtn.disabled = true;
    showStatus(STATUS_MESSAGES[mode], { kw: keyword, n: pagesPerChunkInput.value || 5 });

    try {
      let res;
      if (pendingFile) {
        const fd = new FormData();
        fd.append('file', pendingFile);
        fd.append('mode', mode);
        fd.append('keyword', keyword);
        fd.append('ranges', rangesInput.value.trim());
        fd.append('pages_per_chunk', pagesPerChunkInput.value || 5);
        res = await fetch('/split', { method: 'POST', body: fd });
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

  async function handleLoadPages() {
    splitBtn.disabled = true;
    showStatus(['Reading the PDF…', 'Rendering page previews…', 'Almost there…']);
    pagePicker.classList.add('hidden');
    resultsSection.style.display = 'none';

    try {
      let res;
      if (pendingFile) {
        const fd = new FormData();
        fd.append('file', pendingFile);
        res = await fetch('/preview', { method: 'POST', body: fd });
      } else {
        res = await fetch('/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pdf_url: urlInput.value.trim() }),
        });
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Could not load that PDF.');
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
    selectedPages = new Set(Array.from({ length: totalPages }, (_, i) => i + 1)); // all selected by default
    pageGrid.innerHTML = '';

    for (let n = 1; n <= totalPages; n++) {
      const thumb = document.createElement('div');
      thumb.className = 'page-thumb selected';
      thumb.dataset.page = n;
      thumb.innerHTML = `
        <img src="/thumbnail/${jobId}/${n}" alt="Page ${n}" />
        <div class="page-thumb-check">\u2713</div>
        <div class="page-thumb-num">${n}</div>
      `;
      thumb.addEventListener('click', () => {
        if (selectedPages.has(n)) {
          selectedPages.delete(n);
          thumb.classList.remove('selected');
        } else {
          selectedPages.add(n);
          thumb.classList.add('selected');
        }
        updatePagePickerCount();
      });
      pageGrid.appendChild(thumb);
    }

    updatePagePickerCount();
    pagePicker.classList.remove('hidden');
    hideEmpty();
  }

  function updatePagePickerCount() {
    const n = selectedPages.size;
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
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || 'Something went wrong.');
    }
    resultsCount.textContent = `${data.chapter_count} file${data.chapter_count === 1 ? '' : 's'} · job ${data.job_id}`;
    jobTag.textContent = `job ${data.job_id}`;
    downloadAllBtn.href = data.download_all_url;
    downloadAllBtn.style.display = data.chapter_count > 1 ? 'inline-flex' : 'none';
    renderCards(data.chapters);
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
