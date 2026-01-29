(function () {
  "use strict";

  const STEPPY_STORAGE_KEYS = {
    difficulty: "steppyDifficulty",
    lastSearch: "steppyLastSearch"
  };

  const STATUS_POLL_BASE_MS = 1500;
  const STATUS_POLL_MAX_MS = 8000;
  const STATUS_POLL_HIDDEN_MS = 8000;

  const LOCAL_FALLBACK_THUMBNAIL_URL = "assets/img/core-img/logo.png";

  let latestSearchResultsById = {};
  let statusLoopStarted = false;

  function getCurrentPage() {
    const pageName = document.documentElement.getAttribute("data-steppy-page");
    return pageName || "";
  }

  function setText(elementId, valueText) {
    const element = document.getElementById(elementId);
    if (!element) {
      return;
    }
    element.textContent = valueText == null ? "" : String(valueText);
  }

  function setHtml(elementId, htmlText) {
    const element = document.getElementById(elementId);
    if (!element) {
      return;
    }
    element.innerHTML = htmlText;
  }

  function clampNumber(value, minimumValue, maximumValue) {
    if (!Number.isFinite(value)) {
      return minimumValue;
    }
    return Math.max(minimumValue, Math.min(maximumValue, value));
  }

  function formatElapsedSeconds(elapsedSeconds) {
    const totalSeconds = Math.max(0, Math.floor(Number(elapsedSeconds) || 0));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;

    if (hours > 0) {
      return hours + ":" + String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
    }
    return minutes + ":" + String(seconds).padStart(2, "0");
  }

  function showToast(messageText) {
    const toastElement = document.getElementById("steppy-toast");
    if (!toastElement) {
      return;
    }
    toastElement.classList.add("show");
    toastElement.textContent = messageText;

    window.setTimeout(function () {
      toastElement.classList.remove("show");
    }, 2200);
  }

  function getSelectedDifficulty() {
    const storedDifficulty = window.localStorage.getItem(STEPPY_STORAGE_KEYS.difficulty);
    if (storedDifficulty === "easy" || storedDifficulty === "medium" || storedDifficulty === "hard") {
      return storedDifficulty;
    }
    return "easy";
  }

  function setSelectedDifficulty(difficulty) {
    const cleanedDifficulty = String(difficulty || "").toLowerCase().trim();
    const finalDifficulty = cleanedDifficulty === "easy" || cleanedDifficulty === "medium" || cleanedDifficulty === "hard"
      ? cleanedDifficulty
      : "easy";

    window.localStorage.setItem(STEPPY_STORAGE_KEYS.difficulty, finalDifficulty);
    updateDifficultyButtons(finalDifficulty);
  }

  function updateDifficultyButtons(selectedDifficulty) {
    const difficultyButtons = document.querySelectorAll("[data-steppy-difficulty]");
    difficultyButtons.forEach(function (buttonElement) {
      const buttonDifficulty = String(buttonElement.getAttribute("data-steppy-difficulty") || "");
      if (buttonDifficulty === selectedDifficulty) {
        buttonElement.classList.remove("btn-outline-secondary");
        buttonElement.classList.add("btn-primary");
      } else {
        buttonElement.classList.remove("btn-primary");
        buttonElement.classList.add("btn-outline-secondary");
      }
    });

    setText("steppy-difficulty", selectedDifficulty);
  }

  function uniqueUrlsInOrder(urlList) {
    const seenUrls = new Set();
    const uniqueUrls = [];
    urlList.forEach(function (candidateUrl) {
      const cleanedUrl = (candidateUrl || "").trim();
      if (!cleanedUrl) {
        return;
      }
      if (seenUrls.has(cleanedUrl)) {
        return;
      }
      seenUrls.add(cleanedUrl);
      uniqueUrls.push(cleanedUrl);
    });
    return uniqueUrls;
  }

  function pickThumbnailPlan(videoId, thumbnails) {
    const candidateUrls = [];

    if (Array.isArray(thumbnails)) {
      thumbnails.forEach(function (thumb) {
        if (thumb && typeof thumb.url === "string" && thumb.url) {
          candidateUrls.push(thumb.url);
        }
      });
    }

    const derivedHq = "https://i.ytimg.com/vi/" + encodeURIComponent(videoId) + "/hqdefault.jpg";
    const derivedMq = "https://i.ytimg.com/vi/" + encodeURIComponent(videoId) + "/mqdefault.jpg";
    const derivedDefault = "https://i.ytimg.com/vi/" + encodeURIComponent(videoId) + "/default.jpg";

    candidateUrls.push(derivedHq);
    candidateUrls.push(derivedMq);
    candidateUrls.push(derivedDefault);
    candidateUrls.push(LOCAL_FALLBACK_THUMBNAIL_URL);

    const orderedPreferredUrls = [];
    const orderedOtherUrls = [];

    uniqueUrlsInOrder(candidateUrls).forEach(function (candidateUrl) {
      const lowerUrl = candidateUrl.toLowerCase();
      const isPreferred = lowerUrl.includes("hqdefault") || lowerUrl.includes("mqdefault") || lowerUrl.includes("sddefault");
      const isMaxRes = lowerUrl.includes("maxresdefault") || lowerUrl.includes("maxres");
      if (isPreferred && !isMaxRes) {
        orderedPreferredUrls.push(candidateUrl);
      } else if (!isMaxRes) {
        orderedOtherUrls.push(candidateUrl);
      } else {
        orderedOtherUrls.push(candidateUrl);
      }
    });

    const finalUrlList = uniqueUrlsInOrder(orderedPreferredUrls.concat(orderedOtherUrls));
    const primaryUrl = finalUrlList.length > 0 ? finalUrlList[0] : LOCAL_FALLBACK_THUMBNAIL_URL;
    const fallbackUrls = finalUrlList.slice(1);

    return { primaryUrl: primaryUrl, fallbackUrls: fallbackUrls };
  }

  function attachGlobalImageFallbackHandler() {
    document.addEventListener("error", function (event) {
      const target = event.target;
      if (!(target instanceof HTMLImageElement)) {
        return;
      }

      const fallbackText = target.getAttribute("data-steppy-thumb-fallbacks") || "";
      if (!fallbackText) {
        return;
      }

      const remainingUrls = fallbackText.split("|").map(function (entry) {
        return entry.trim();
      }).filter(Boolean);

      if (remainingUrls.length === 0) {
        target.removeAttribute("data-steppy-thumb-fallbacks");
        return;
      }

      const nextUrl = remainingUrls.shift();
      target.setAttribute("data-steppy-thumb-fallbacks", remainingUrls.join("|"));
      target.src = nextUrl;
    }, true);
  }

  async function fetchJson(urlPath, fetchOptions) {
    let response;
    try {
      response = await window.fetch(urlPath, fetchOptions || {});
    } catch (error) {
      return { ok: false, status: 0, data: null, error: String(error) };
    }

    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }

    return { ok: response.ok, status: response.status, data: data, error: null };
  }

  async function probeBackend() {
    const result = await fetchJson("/api/status", { cache: "no-store" });
    return Boolean(result.ok && result.data && result.data.ok);
  }

  function setBackendStatusText(backendAvailable) {
    const badgeElement = document.getElementById("steppy-backend");
    if (!badgeElement) {
      return;
    }
    badgeElement.textContent = backendAvailable ? "Backend connected" : "Backend not connected";
    badgeElement.classList.toggle("steppy-bad", !backendAvailable);
  }

  function setBadgeState(badgeId, stateText) {
    const badgeElement = document.getElementById(badgeId);
    if (!badgeElement) {
      return;
    }

    const cleanedState = String(stateText || "").toUpperCase();
    badgeElement.textContent = cleanedState;

    badgeElement.classList.remove("steppy-state-idle", "steppy-state-loading", "steppy-state-playing", "steppy-state-paused", "steppy-state-error");

    if (cleanedState === "PLAYING") {
      badgeElement.classList.add("steppy-state-playing");
    } else if (cleanedState === "PAUSED") {
      badgeElement.classList.add("steppy-state-paused");
    } else if (cleanedState === "LOADING") {
      badgeElement.classList.add("steppy-state-loading");
    } else if (cleanedState === "ERROR") {
      badgeElement.classList.add("steppy-state-error");
    } else {
      badgeElement.classList.add("steppy-state-idle");
    }
  }

  function bindCommonHandlers() {
    const difficultyButtons = document.querySelectorAll("[data-steppy-difficulty]");
    difficultyButtons.forEach(function (buttonElement) {
      buttonElement.addEventListener("click", function (event) {
        event.preventDefault();
        const difficultyValue = buttonElement.getAttribute("data-steppy-difficulty") || "easy";
        setSelectedDifficulty(difficultyValue);
      });
    });

    updateDifficultyButtons(getSelectedDifficulty());
  }

  function bindControllerHandlers(backendAvailable) {
    const controlButtons = document.querySelectorAll("[data-steppy-action]");
    controlButtons.forEach(function (buttonElement) {
      buttonElement.addEventListener("click", async function (event) {
        event.preventDefault();
        if (!backendAvailable) {
          showToast("Backend not connected");
          return;
        }

        const actionName = String(buttonElement.getAttribute("data-steppy-action") || "");
        const actionPath = "/api/" + actionName;

        const result = await fetchJson(actionPath, { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store" });
        if (!result.ok || !result.data || !result.data.ok) {
          showToast("Action failed");
        }
      });
    });
  }

  function safeText(textValue) {
    return String(textValue || "").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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

    videoResults.forEach(function (item) {
      const videoId = String(item.video_id || "");
      if (!videoId) {
        return;
      }

      latestSearchResultsById[videoId] = item;

      const titleText = safeText(item.title || "");
      const channelText = safeText(item.channel_title || "");

      const durationSeconds = Number(item.duration_seconds || 0);
      const durationText = formatElapsedSeconds(durationSeconds);

      const thumbnailPlan = pickThumbnailPlan(videoId, item.thumbnails);
      const fallbackData = thumbnailPlan.fallbackUrls.join("|");

      const columnElement = document.createElement("div");
      columnElement.className = "col-12";

      const cardHtml = [
        '<div class="card steppy-result-card steppy-search-card">',
        '  <div class="card-body">',
        '    <div class="d-flex align-items-center">',
        '      <div class="steppy-thumb-box">',
        '        <a class="d-block" href="#" data-steppy-play="' + videoId + '">',
        '          <img class="steppy-thumb" loading="lazy" decoding="async" src="' + thumbnailPlan.primaryUrl + '" data-steppy-thumb-fallbacks="' + fallbackData + '" alt="">',
        '        </a>',
        '      </div>',
        '      <div class="steppy-result-content flex-grow-1 ms-3">',
        '        <a class="steppy-result-title d-block text-truncate" href="#" data-steppy-play="' + videoId + '">' + titleText + '</a>',
        '        <div class="small steppy-muted text-truncate">' + channelText + '</div>',
        '        <div class="d-flex align-items-center justify-content-between mt-2">',
        '          <div class="small steppy-muted">' + durationText + '</div>',
        '          <button class="btn btn-primary btn-sm" type="button" data-steppy-play="' + videoId + '">Play</button>',
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
    const clearButton = document.getElementById("steppy-search-clear");

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
      if (!trimmedQuery) {
        renderSearchResults([]);
        return;
      }

      if (!backendAvailable) {
        showToast("Backend not connected");
        return;
      }

      window.localStorage.setItem(STEPPY_STORAGE_KEYS.lastSearch, trimmedQuery);

      const myToken = activeSearchToken + 1;
      activeSearchToken = myToken;

      setHtml("steppy-search-status", '<span class="steppy-muted">Searching...</span>');

      const url = "/api/search?q=" + encodeURIComponent(trimmedQuery) + (pageToken ? "&page_token=" + encodeURIComponent(pageToken) : "");
      const result = await fetchJson(url, { cache: "no-store" });

      if (activeSearchToken !== myToken) {
        return;
      }

      if (!result.ok || !result.data || !result.data.ok) {
        setHtml("steppy-search-status", '<span class="steppy-muted">Search failed</span>');
        renderSearchResults([]);
        return;
      }

      const response = result.data.response || {};
      const items = response.items || [];
      renderSearchResults(items);

      const totalResults = Number(response.total_results || 0);
      const shownCount = Array.isArray(items) ? items.length : 0;
      if (totalResults > 0) {
        setHtml("steppy-search-status", '<span class="steppy-muted">Showing ' + shownCount + " of " + totalResults + "</span>");
      } else {
        setHtml("steppy-search-status", '<span class="steppy-muted">Showing ' + shownCount + "</span>");
      }
    }

    let searchDebounceTimer = null;

    function scheduleSearch() {
      if (searchDebounceTimer) {
        window.clearTimeout(searchDebounceTimer);
      }
      searchDebounceTimer = window.setTimeout(function () {
        runSearch(searchInput.value, null);
      }, 250);
    }

    searchInput.addEventListener("input", function () {
      scheduleSearch();
      if (clearButton) {
        clearButton.classList.toggle("d-none", !searchInput.value);
      }
    });

    searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      runSearch(searchInput.value, null);
    });

    if (clearButton) {
      clearButton.addEventListener("click", function (event) {
        event.preventDefault();
        searchInput.value = "";
        clearButton.classList.add("d-none");
        renderSearchResults([]);
      });
    }

    document.body.addEventListener("click", async function (event) {
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }

      const playButton = target.closest("[data-steppy-play]");
      if (!playButton) {
        return;
      }

      event.preventDefault();

      if (!backendAvailable) {
        showToast("Backend not connected");
        return;
      }

      const videoId = String(playButton.getAttribute("data-steppy-play") || "");
      if (!videoId) {
        return;
      }

      const selectedDifficulty = getSelectedDifficulty();
      const videoData = latestSearchResultsById[videoId] || {};
      const thumbnailPlan = pickThumbnailPlan(videoId, videoData.thumbnails);

      const payload = {
        video_id: videoId,
        difficulty: selectedDifficulty,
        video_title: videoData.title || null,
        channel_title: videoData.channel_title || null,
        thumbnail_url: thumbnailPlan.primaryUrl,
        duration_seconds: videoData.duration_seconds || 0
      };

      const result = await fetchJson("/api/play", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        cache: "no-store"
      });

      if (!result.ok || !result.data || !result.data.ok) {
        showToast("Play failed");
        return;
      }

      window.location.href = "/controller.html";
    });

    if (searchInput.value) {
      runSearch(searchInput.value, null);
    }
  }

  async function pollStatusOnce() {
    const result = await fetchJson("/api/status", { cache: "no-store" });
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
      setText("steppy-song-channel", channelTitle || "");
      setText("steppy-video-id", videoId);
    } else {
      setText("steppy-song-title", "Idle");
      setText("steppy-song-channel", "Select a song from Search");
      setText("steppy-video-id", "");
    }

    setText("steppy-elapsed", formatElapsedSeconds(elapsedSeconds));
    setText("steppy-difficulty", difficultyText);
    setBadgeState("steppy-state-badge", stateText);
  }

  function isPageVisible() {
    return document.visibilityState === "visible";
  }

  async function startStatusLoop() {
    if (statusLoopStarted) {
      return;
    }
    statusLoopStarted = true;

    let currentDelayMs = STATUS_POLL_BASE_MS;

    async function tick() {
      if (!isPageVisible()) {
        currentDelayMs = STATUS_POLL_HIDDEN_MS;
        window.setTimeout(tick, currentDelayMs);
        return;
      }

      try {
        const status = await pollStatusOnce();
        if (status) {
          updateControllerFromStatus(status);
          currentDelayMs = STATUS_POLL_BASE_MS;
        } else {
          currentDelayMs = clampNumber(currentDelayMs * 1.6, STATUS_POLL_BASE_MS, STATUS_POLL_MAX_MS);
        }
      } catch (error) {
        currentDelayMs = clampNumber(currentDelayMs * 1.6, STATUS_POLL_BASE_MS, STATUS_POLL_MAX_MS);
      }

      window.setTimeout(tick, currentDelayMs);
    }

    document.addEventListener("visibilitychange", function () {
      if (isPageVisible()) {
        currentDelayMs = STATUS_POLL_BASE_MS;
      }
    });

    tick();
  }

  async function initialize() {
    attachGlobalImageFallbackHandler();
    bindCommonHandlers();

    const backendAvailable = await probeBackend();

    if (getCurrentPage() === "controller") {
      setBackendStatusText(backendAvailable);
      bindControllerHandlers(backendAvailable);
      if (backendAvailable) {
        startStatusLoop();
      }
    } else if (getCurrentPage() === "search") {
      setBackendStatusText(backendAvailable);
      bindSearchHandlers(backendAvailable);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    initialize();
  });
})();