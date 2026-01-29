(function () {
  "use strict";

  const STEPPY_STORAGE_KEYS = {
    difficulty: "steppyDifficulty",
    lastSearch: "steppyLastSearch"
  };

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

  function setBadgeStyle(elementId, stateText) {
    const badgeElement = document.getElementById(elementId);
    if (!badgeElement) {
      return;
    }

    badgeElement.classList.remove("bg-secondary", "bg-success", "bg-warning", "bg-danger", "bg-info");

    const normalizedState = String(stateText || "").toUpperCase();
    if (normalizedState === "PLAYING") {
      badgeElement.classList.add("bg-success");
    } else if (normalizedState === "PAUSED") {
      badgeElement.classList.add("bg-warning");
    } else if (normalizedState === "ERROR") {
      badgeElement.classList.add("bg-danger");
    } else if (normalizedState === "LOADING") {
      badgeElement.classList.add("bg-info");
    } else {
      badgeElement.classList.add("bg-secondary");
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

  function showToast(message, bootstrapBackgroundClass) {
    const toastParts = ensureToast();
    if (!toastParts) {
      return;
    }

    const { toastElement, toastBodyElement } = toastParts;
    toastBodyElement.textContent = String(message);

    toastElement.classList.remove("text-bg-primary", "text-bg-success", "text-bg-warning", "text-bg-danger", "text-bg-info");
    toastElement.classList.add(bootstrapBackgroundClass || "text-bg-primary");

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
    if (!response.ok) {
      return { ok: false, status: response.status, data: null };
    }

    const data = await response.json();
    return { ok: true, status: response.status, data };
  }

  async function probeBackend() {
    try {
      const probeResult = await fetchJson("/api/status");
      return probeResult.ok;
    } catch (error) {
      return false;
    }
  }

  function setBackendStatusText(isAvailable) {
    const backendStatusText = isAvailable ? "connected" : "not connected";
    setText("steppy-backend-status", backendStatusText);
  }

  async function postCommand(endpointPath, payloadObject) {
    const requestBody = payloadObject ? JSON.stringify(payloadObject) : "{}";
    const result = await fetchJson(endpointPath, {
      method: "POST",
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json"
      },
      body: requestBody
    });
    return result.ok;
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

  function getDemoSearchResults(queryText) {
    const trimmedQuery = String(queryText || "").trim();
    const titleSuffix = trimmedQuery ? (" for \"" + trimmedQuery + "\"") : "";
    return [
      {
        video_id: "dQw4w9WgXcQ",
        title: "Demo result" + titleSuffix,
        duration_seconds: 213,
        thumbnail_url: "assets/img/bg-img/p1.jpg"
      },
      {
        video_id: "kxopViU98Xo",
        title: "Another demo result" + titleSuffix,
        duration_seconds: 180,
        thumbnail_url: "assets/img/bg-img/p2.jpg"
      },
      {
        video_id: "J---aiyznGQ",
        title: "One more demo result" + titleSuffix,
        duration_seconds: 196,
        thumbnail_url: "assets/img/bg-img/p3.jpg"
      }
    ];
  }

  function renderSearchResults(videoResults) {
    const resultsGrid = document.getElementById("steppy-results-grid");
    if (!resultsGrid) {
      return;
    }

    resultsGrid.innerHTML = "";

    if (!videoResults || videoResults.length === 0) {
      const emptyElement = document.createElement("div");
      emptyElement.className = "col-12";
      emptyElement.innerHTML = '<div class="alert alert-secondary mb-0">No results</div>';
      resultsGrid.appendChild(emptyElement);
      return;
    }

    videoResults.forEach(function (resultItem) {
      const videoId = String(resultItem.video_id || "");
      const titleText = String(resultItem.title || "Untitled");
      const durationText = formatElapsedSeconds(Number(resultItem.duration_seconds || 0));
      const thumbnailUrl = String(resultItem.thumbnail_url || "assets/img/bg-img/p1.jpg");

      const columnElement = document.createElement("div");
      columnElement.className = "col-12";

      const cardHtml = [
        '<div class="card single-product-card">',
        '  <div class="card-body">',
        '    <div class="d-flex align-items-center">',
        '      <div class="card-side-img">',
        '        <a class="product-thumbnail d-block" href="#" data-steppy-play="' + videoId + '">',
        '          <img src="' + thumbnailUrl + '" alt="">',
        '        </a>',
        '      </div>',
        '      <div class="card-content px-4 py-2 w-100">',
        '        <a class="product-title d-block text-truncate mt-0" href="#" data-steppy-play="' + videoId + '">' + titleText + '</a>',
        '        <p class="sale-price mb-2">' + durationText + '</p>',
        '        <button class="btn btn-primary btn-sm" type="button" data-steppy-play="' + videoId + '">Play</button>',
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

    async function runSearch(queryText) {
      const trimmedQuery = String(queryText || "").trim();
      window.localStorage.setItem(STEPPY_STORAGE_KEYS.lastSearch, trimmedQuery);

      if (!backendAvailable) {
        const demoResults = getDemoSearchResults(trimmedQuery);
        renderSearchResults(demoResults);
        showToast("Demo search results loaded", "text-bg-info");
        return;
      }

      const encodedQuery = encodeURIComponent(trimmedQuery);
      const result = await fetchJson("/api/search?q=" + encodedQuery);
      if (!result.ok) {
        showToast("Search failed", "text-bg-danger");
        return;
      }

      const resultList = Array.isArray(result.data && result.data.results) ? result.data.results : [];
      renderSearchResults(resultList);
    }

    searchForm.addEventListener("submit", function (event) {
      event.preventDefault();
      runSearch(searchInput.value);
    });

    runSearch(searchInput.value);
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

      const selectedDifficulty = getSelectedDifficulty();

      if (!backendAvailable) {
        showToast("Play queued (demo): " + videoId, "text-bg-info");
        return;
      }

      const ok = await postCommand("/api/play", { video_id: videoId, difficulty: selectedDifficulty });
      if (ok) {
        showToast("Play requested", "text-bg-success");
        window.location.href = "controller.html";
      } else {
        showToast("Play failed", "text-bg-danger");
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
          showToast("Difficulty set (local): " + difficultyValue, "text-bg-info");
          return;
        }

        const ok = await postCommand("/api/difficulty", { difficulty: difficultyValue });
        if (ok) {
          showToast("Difficulty updated", "text-bg-success");
        } else {
          showToast("Difficulty update failed", "text-bg-danger");
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
          showToast("Command ignored (no backend): " + commandName, "text-bg-warning");
          return;
        }

        const endpoint = "/api/" + encodeURIComponent(commandName);
        const ok = await postCommand(endpoint, null);
        if (ok) {
          showToast("Command sent: " + commandName, "text-bg-success");
        } else {
          showToast("Command failed: " + commandName, "text-bg-danger");
        }
      });
    });
  }

  async function pollStatusLoop() {
    const currentPage = getCurrentPage();
    if (currentPage !== "controller") {
      return;
    }

    try {
      const result = await fetchJson("/api/status");
      if (!result.ok) {
        return;
      }

      const status = result.data || {};
      const stateText = String(status.state || "IDLE");
      const videoId = status.video_id ? String(status.video_id) : "";
      const elapsedSeconds = Number(status.elapsed_seconds || 0);
      const difficultyText = status.difficulty ? String(status.difficulty) : getSelectedDifficulty();

      if (videoId) {
        setText("steppy-song-title", "Video " + videoId);
        setText("steppy-song-meta", "Video id: " + videoId);
      } else {
        setText("steppy-song-title", "No song selected");
        setText("steppy-song-meta", "Select a song from Search");
      }

      setText("steppy-elapsed", formatElapsedSeconds(elapsedSeconds));
      setText("steppy-difficulty", difficultyText);
      setBadgeStyle("steppy-state-badge", stateText);
    } catch (error) {
      return;
    }
  }

  async function initialize() {
    const backendAvailable = await probeBackend();

    if (getCurrentPage() === "controller") {
      setBackendStatusText(backendAvailable);
      bindControllerHandlers(backendAvailable);

      if (backendAvailable) {
        setInterval(pollStatusLoop, 500);
        pollStatusLoop();
      }
    }

    if (getCurrentPage() === "search") {
      bindSearchHandlers(backendAvailable);
    }

    bindPlayClickHandlers(backendAvailable);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initialize().catch(function () {
      showToast("Initialization failed", "text-bg-danger");
    });
  });
})();