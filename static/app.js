(function () {
  const form = document.getElementById('splitForm');
  const urlInput = document.getElementById('pdfUrl');
  const splitBtn = document.getElementById('splitBtn');
  const kwToggle = document.getElementById('kwToggle');
  const kwToggleWrap = document.getElementById('kwToggleWrap');
  const modeToggle = document.getElementById('modeToggle');
  const uploadAltBtn = document.getElementById('uploadAltBtn');
  const fileInput = document.getElementById('fileInput');

  const status = document.getElementById('status');
  const statusText = document.getElementById('statusText');
  const errorPanel = document.getElementById('errorPanel');
  const errorText = document.getElementById('errorText');
  const emptyState = document.getElementById('emptyState');

  const resultsSection = document.getElementById('resultsSection');
  const resultsCount = document.getElementById('resultsCount');
  const cardsList = document.getElementById('cardsList');
  const downloadAllBtn = document.getElementById('downloadAllBtn');
  const jobTag = document.getElementById('jobTag');

  let keyword = 'Unit';
  let mode = 'unit';
  let pendingFile = null;

  const STRIPES = ['var(--brass)', 'var(--sage)', 'var(--teal)', 'var(--dusty-rose)'];

  const STATUS_MESSAGES_UNIT = [
    'Reading the table of contents…',
    'Finding every "{kw}" heading…',
    'Cutting along the seams…',
    'Stitching chapter files together…',
  ];

  const STATUS_MESSAGES_LESSON = [
    'Reading the table of contents…',
    'Finding every numbered lesson…',
    'Cutting along the seams…',
    'Stitching lesson files together…',
  ];

  const STATUS_MESSAGES_COMBINED = [
    'Reading the table of contents…',
    'Finding every "{kw}" and its lessons…',
    'Building a folder per unit…',
    'Stitching everything together…',
  ];

  modeToggle.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-mode]');
    if (!btn) return;
    mode = btn.dataset.mode;
    [...modeToggle.querySelectorAll('button')].forEach((b) => b.classList.toggle('active', b === btn));
    kwToggleWrap.classList.toggle('hidden', mode === 'lesson');
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
      urlInput.required = false;
    }
  });
  urlInput.addEventListener('input', () => {
    pendingFile = null;
  });

  function showEmpty() {
    emptyState.classList.add('visible');
    resultsSection.style.display = 'none';
  }

  function hideEmpty() {
    emptyState.classList.remove('visible');
  }

  function showStatus() {
    status.classList.remove('error');
    status.classList.add('visible');
    const messages =
      mode === 'lesson' ? STATUS_MESSAGES_LESSON :
      mode === 'combined' ? STATUS_MESSAGES_COMBINED :
      STATUS_MESSAGES_UNIT;
    let i = 0;
    statusText.textContent = messages[0].replace('{kw}', keyword);
    status._interval = setInterval(() => {
      i = (i + 1) % messages.length;
      statusText.textContent = messages[i].replace('{kw}', keyword);
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

  async function handleSubmit(e) {
    e.preventDefault();
    hideError();
    hideEmpty();
    resultsSection.style.display = 'none';

    if (!pendingFile && !urlInput.value.trim()) {
      showError('Paste a PDF link, or choose a file to upload.');
      return;
    }

    splitBtn.disabled = true;
    showStatus();

    try {
      let res;
      if (pendingFile) {
        const fd = new FormData();
        fd.append('file', pendingFile);
        fd.append('mode', mode);
        fd.append('keyword', keyword);
        res = await fetch('/split', { method: 'POST', body: fd });
      } else {
        res = await fetch('/split', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pdf_url: urlInput.value.trim(), mode, keyword }),
        });
      }

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || 'Something went wrong while splitting that PDF.');
      }

      resultsCount.textContent = `${data.chapter_count} file${data.chapter_count === 1 ? '' : 's'} · job ${data.job_id}`;
      jobTag.textContent = `job ${data.job_id}`;
      downloadAllBtn.href = data.download_all_url;
      renderCards(data.chapters);
      resultsSection.style.display = 'block';
    } catch (err) {
      showError(err.message || String(err));
      showEmpty();
    } finally {
      hideStatus();
      splitBtn.disabled = false;
    }
  }

  form.addEventListener('submit', handleSubmit);

  showEmpty();
})();
