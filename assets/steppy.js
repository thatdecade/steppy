(function () {
  "use strict";

  const STEPPY_STORAGE_KEYS = {
    difficulty: "steppyDifficulty",
    lastSearch: "steppyLastSearch"
  };

  let latestSearchResultsById = {};

  function getCurrentPage() {
    const pageName = document.documentElement.getAttribute("data-steppy-page");
    return pageName || "";
  }

  function formatElapsedSeconds(elapsedSeconds) {
    if (!Number.isFinite(elapsedSeconds) || elapsedSeconds < 0) {
      return "0:00";
    }
    const totalSeconds = Math.floor(elapsedSeconds);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    const secondsText = seconds < 10 ? "0" + String(seconds) : String(seconds);
    return String(minutes) + ":" + secondsText;
  }

  function getSelectedDifficulty() {
    const storedDifficulty = window.localStorage.getItem(STEPPY_STORAGE_KEYS.difficulty);
    if (storedDifficulty === "easy" || storedDifficulty === "medium" || storedDifficulty === "hard") {
      return storedDifficulty;
    }
    return "easy";
  }

  function setSelectedDifficulty(difficulty) {
    window.localStorage.setItem(STEPPY_STORAGE_KEYS.difficulty, difficulty);
  }

  function setText(elementId, textValue) {
    const element = document.getElementById(elementId);
    if (!element) {
      return;
    }
    element.textContent = textValue;
  }

  function setBadgeState(elementId, stateText) {
    const badgeElement = document.getElementById(elementId);
    if (!badgeElement) {
      return;
    }

    const normalizedState = String(stateText || "").toUpperCase();
    badgeElement.classList.remove("steppy-state-playing", "steppy-state-paused", "steppy-state-loading", "steppy-state-error");

    if (normalizedState === "PLAYING") {
      badgeElement.classList.add("steppy-state-playing");
    } else if (normalizedState === "PAUSED") {
      badgeElement.classList.add("steppy-state-paused");
    } else if (normalizedState === "LOADING") {
      badgeElement.classList.add("steppy-state-loading");
    } else if (normalizedState === "ERROR") {
      badgeElement.classList.add("steppy-state-error");
    }

    badgeElement.textContent = normalizedState || "IDLE";
  }

  function ensureToast() {
    const toastElement = document.getElementById("steppy-toast");
    const toastBodyElement = document.getElementById("steppy-toast-body");
    if (!toastElement || !toastBodyElement) {
      return null;
    }
    return { toastElement, toastBodyElement };
  }

  function showToast(message) {
    const toastParts = ensureToast();
    if (!toastParts) {
      return;
    }

    const { toastElement, toastBodyElement } = toastParts;
    toastBodyElement.textContent = String(message);

    if (!window.bootstrap || !window.bootstrap.Toast) {
      return;
    }

    const toastInstance = window.bootstrap.Toast.getOrCreateInstance(toastElement, { delay: 1800 });
    toastInstance.show();
  }

  async function fetchJson(url, fetchOptions) {
    const options = Object.assign(
      {
        cache: "no-store",
        headers: {
          "Accept": "application/json"
        }
      },
      fetchOptions || {}
    );

    const response = await fetch(url, options);
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return { ok: false, status: response.status, data: null };
    }

    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
  }

  async function probeBackend() {
    try {
      const probeResult = await fetchJson("/api/status");
      return Boolean(probeResult.ok && probeResult.data && probeResult.data.ok);
    } catch (error) {
      return false;
    }
  }

  function setBackendStatusText(isAvailable) {
    const backendStatusText = isAvailable ? "connected" : "not connected";
    setText("steppy-backend-status", backendStatusText);
  }

  async function postJson(endpointPath, payloadObject) {
    const requestBody = payloadObject ? JSON.stringify(payloadObject) : "{}";
    const result = await fetchJson(endpointPath, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json"
      },
      body: requestBody
    });
    return Boolean(result.ok && result.data && result.data.ok);
  }

  function applyDifficultyButtonState(selectedDifficulty) {
    const difficultyButtons = document.querySelectorAll("[data-steppy-difficulty]");
    difficultyButtons.forEach(function (buttonElement) {
      const difficultyValue = buttonElement.getAttribute("data-steppy-difficulty");
      if (difficultyValue === selectedDifficulty) {
        buttonElement.classList.remove("btn-outline-secondary");
        buttonElement.classList.add("btn-primary");
      } else {
        buttonElement.classList.remove("btn-primary");
        buttonElement.classList.add("btn-outline-secondary");
      }
    });

    setText("steppy-difficulty", selectedDifficulty);
  }

  function pickBestThumbnailUrl(thumbnails) {
    if (!Array.isArray(thumbnails) || thumbnails.length === 0) {
      return "assets/img/bg-img/1.jpg";
    }
    const first = thumbnails[0];
    if (first && typeof first.url === "string" && first.url) {
      return first.url;
    }
    return "assets/img/bg-img/1.jpg";
  }

  function renderSearchResults(videoResults) {
    const resultsGrid = document.getElementById("steppy-results-grid");
    if (!resultsGrid) {
      return;
    }

    latestSearchResultsById = {};
    resultsGrid.innerHTML = "";

    if (!videoResults || videoResults.length === 0) {
      const emptyElement = document.createElement("div");
      emptyElement.className = "col-12";
      emptyElement.innerHTML = '<div class="alert steppy-alert mb-0">No results</div>';
      resultsGrid.appendChild(emptyElement);
      return;
    }

    videoResults.forEach(function (resultItem) {
      const videoId = String(resultItem.video_id || "");
      const titleText = String(resultItem.title || "Untitled");
      const channelText = String(resultItem.channel_title || "");
      const durationText = formatElapsedSeconds(Number(resultItem.duration_seconds || 0));
      const thumbnailUrl = pickBestThumbnailUrl(resultItem.thumbnails);

      latestSearchResultsById[videoId] = {
        video_id: videoId,
        title: titleText,
        channel_title: channelText,
        duration_seconds: Number(resultItem.duration_seconds || 0),
        thumbnail_url: thumbnailUrl
      };

      const columnElement = document.createElement("div");
      columnElement.className = "col-12";

      const safeTitle = titleText.replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const safeChannel = channelText.replace(/</g, "&lt;").replace(/>/g, "&gt;");

      const cardHtml = [
        '<div class="card steppy-result-card">',
        '  <div class="card-body">',
        '    <div class="d-flex align-items-center">',
        '      <div class="card-side-img">',
        '        <a class="product-thumbnail d-block" href="#" data-steppy-play="' + videoId + '">',
        '          <img class="steppy-thumb" src="' + thumbnailUrl + '" alt="">',
        '        </a>',
        '      </div>',
        '      <div class="card-content px-3 py-1 w-100">',
        '        <a class="product-title d-block text-truncate mt-0" href="#" data-steppy-play="' + videoId + '">' + safeTitle + '</a>',
        '        <div class="small steppy-muted text-truncate">' + safeChannel + '</div>',
        '        <div class="d-flex align-items-center justify-content-between mt-2">',
        '          <div class="small steppy-muted">' + durationText + '</div>',
        '          <button class="btn btn-primary btn-sm steppy-btn" type="button" data-steppy-play="' + videoId + '">Play</button>',
        '        </div>',
        '      </div>',
        '    </div>',
        '  </div>',
        '</div>'
      ].join("");

      columnElement.innerHTML = cardHtml;
      resultsGrid.appendChild(columnElement);
    });
  }

  function bindSearchHandlers(backendAvailable) {
    const searchForm = document.getElementById("steppy-search-form");
    const searchInput = document.getElementById("steppy-search-input");
    if (!searchForm || !searchInput) {
      return;
    }

    const lastSearch = window.localStorage.getItem(STEPPY_STORAGE_KEYS.lastSearch);
    if (lastSearch) {
      searchInput.value = lastSearch;
    }

    let activeSearchToken = 0;

    async function runSearch(queryText, pageToken) {
      const trimmedQuery = String(queryText || "").trim();
      if (!backendAvailable) {
        showToast("Backend not connected");
        return;
      }

      window.localStorage.setItem(STEPPY_STORAGE_KEYS.lastSearch, trimmedQuery);

      const localToken = activeSearchToken + 1;
      activeSearchToken = localToken;

      const encodedQuery = encodeURIComponent(trimmedQuery);
      const url = pageToken ? ("/api/search?q=" + encodedQuery + "&page_token=" + encodeURIComponent(pageToken)) : ("/api/search?q=" + encodedQuery);

      const result = await fetchJson(url);
      if (activeSearchToken !== localToken) {
        return;
      }

      if (!result.ok || !result.data || !result.data.ok) {
        showToast("Search failed");
        renderSearchResults([]);
        return;
      }

      const responseBlock = result.data.response || {};
      const items = Array.isArray(responseBlock.items) ? responseBlock.items : [];
      renderSearchResults(items);
    }

    searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      runSearch(searchInput.value, null);
    });

    if (searchInput.value.trim()) {
      runSearch(searchInput.value, null);
    }
  }

  function bindPlayClickHandlers(backendAvailable) {
    document.addEventListener("click", async function (event) {
      const targetElement = event.target;
      if (!(targetElement instanceof Element)) {
        return;
      }

      const playElement = targetElement.closest("[data-steppy-play]");
      if (!playElement) {
        return;
      }

      event.preventDefault();

      const videoId = playElement.getAttribute("data-steppy-play") || "";
      if (!videoId) {
        return;
      }

      if (!backendAvailable) {
        showToast("Backend not connected");
        return;
      }

      const selectedDifficulty = getSelectedDifficulty();
      const details = latestSearchResultsById[videoId] || null;
      const playPayload = {
        video_id: videoId,
        difficulty: selectedDifficulty,
        video_title: details ? details.title : null,
        channel_title: details ? details.channel_title : null,
        duration_seconds: details ? details.duration_seconds : null,
        thumbnail_url: details ? details.thumbnail_url : null
      };
      const ok = await postJson("/api/play", playPayload);
      if (ok) {
        showToast("Play requested");
        window.location.href = "controller.html";
      } else {
        showToast("Play failed");
      }
    });
  }

  function bindControllerHandlers(backendAvailable) {
    applyDifficultyButtonState(getSelectedDifficulty());

    const difficultyButtons = document.querySelectorAll("[data-steppy-difficulty]");
    difficultyButtons.forEach(function (buttonElement) {
      buttonElement.addEventListener("click", async function () {
        const difficultyValue = buttonElement.getAttribute("data-steppy-difficulty");
        if (!difficultyValue) {
          return;
        }

        setSelectedDifficulty(difficultyValue);
        applyDifficultyButtonState(difficultyValue);

        if (!backendAvailable) {
          showToast("Backend not connected");
          return;
        }

        const ok = await postJson("/api/difficulty", { difficulty: difficultyValue });
        if (!ok) {
          showToast("Difficulty update failed");
        }
      });
    });

    const commandButtons = document.querySelectorAll("[data-steppy-command]");
    commandButtons.forEach(function (buttonElement) {
      buttonElement.addEventListener("click", async function () {
        const commandName = buttonElement.getAttribute("data-steppy-command");
        if (!commandName) {
          return;
        }

        if (!backendAvailable) {
          showToast("Backend not connected");
          return;
        }

        const endpoint = "/api/" + encodeURIComponent(commandName);
        const ok = await postJson(endpoint, null);
        if (!ok) {
          showToast("Command failed: " + commandName);
        }
      });
    });
  }

  async function pollStatusOnce() {
    const result = await fetchJson("/api/status");
    if (!result.ok || !result.data || !result.data.ok) {
      return null;
    }
    return result.data;
  }

  function updateControllerFromStatus(status) {
    const stateText = String(status.state || "IDLE");
    const videoId = status.video_id ? String(status.video_id) : "";
    const videoTitle = status.video_title ? String(status.video_title) : "";
    const channelTitle = status.channel_title ? String(status.channel_title) : "";
    const elapsedSeconds = Number(status.elapsed_seconds || 0);
    const difficultyText = status.difficulty ? String(status.difficulty) : getSelectedDifficulty();

    if (videoId) {
      const shownTitle = videoTitle ? videoTitle : ("Video " + videoId);
      setText("steppy-song-title", shownTitle);
      if (channelTitle) {
        setText("steppy-song-meta", channelTitle);
      } else {
        setText("steppy-song-meta", "Video id: " + videoId);
      }
    } else {
      setText("steppy-song-title", "No song selected");
      setText("steppy-song-meta", "Select a song from Search");
    }

    setText("steppy-elapsed", formatElapsedSeconds(elapsedSeconds));
    setText("steppy-difficulty", difficultyText);
    setBadgeState("steppy-state-badge", stateText);
  }

  async function startStatusLoop() {
    let backoffMs = 500;

    async function tick() {
      try {
        const status = await pollStatusOnce();
        if (status) {
          updateControllerFromStatus(status);
          backoffMs = 500;
        } else {
          backoffMs = Math.min(4000, Math.floor(backoffMs * 1.5));
        }
      } catch (error) {
        backoffMs = Math.min(4000, Math.floor(backoffMs * 1.5));
      }

      window.setTimeout(tick, backoffMs);
    }

    tick();
  }

  async function initialize() {
    const backendAvailable = await probeBackend();

    if (getCurrentPage() === "controller") {
      setBackendStatusText(backendAvailable);
      bindControllerHandlers(backendAvailable);
      if (backendAvailable) {
        startStatusLoop();
      }
    }

    if (getCurrentPage() === "search") {
      bindSearchHandlers(backendAvailable);
    }

    bindPlayClickHandlers(backendAvailable);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initialize().catch(function () {
      showToast("Initialization failed");
    });
  });
})();
