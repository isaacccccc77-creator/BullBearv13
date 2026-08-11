(function () {
  "use strict";

  const STORAGE_KEY = "quizit.decks.v1";
  const DISMISS_KEY = "quizit.installDismissed";

  // ---------------------------------------------------------------
  // Storage
  // ---------------------------------------------------------------
  function loadDecks() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    } catch (e) {
      return [];
    }
  }
  function saveDecks(decks) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(decks));
  }
  function upsertDeck(deck) {
    const decks = loadDecks();
    const idx = decks.findIndex((d) => d.id === deck.id);
    if (idx >= 0) decks[idx] = deck;
    else decks.unshift(deck);
    saveDecks(decks);
  }
  function deleteDeck(id) {
    saveDecks(loadDecks().filter((d) => d.id !== id));
  }

  // ---------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------
  function $(sel) { return document.querySelector(sel); }
  function $all(sel) { return Array.from(document.querySelectorAll(sel)); }
  function vibrate(ms) { if (navigator.vibrate) { try { navigator.vibrate(ms); } catch (e) {} } }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
  function timeAgo(ts) {
    const diff = Date.now() - ts;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    const days = Math.floor(hrs / 24);
    if (days < 7) return days + "d ago";
    return new Date(ts).toLocaleDateString();
  }
  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
  }

  function showScreen(id) {
    $all(".screen").forEach((s) => s.classList.toggle("active", s.id === id));
    window.scrollTo(0, 0);
  }

  // ---------------------------------------------------------------
  // App state
  // ---------------------------------------------------------------
  let currentDeck = null;

  // ---------------------------------------------------------------
  // HOME
  // ---------------------------------------------------------------
  function renderHome() {
    const decks = loadDecks();
    const wrap = $("#saved-decks-wrap");
    const list = $("#saved-decks");
    list.innerHTML = "";
    if (decks.length === 0) {
      wrap.classList.add("hidden");
      return;
    }
    wrap.classList.remove("hidden");
    for (const deck of decks) {
      const card = document.createElement("div");
      card.className = "deck-card";
      card.innerHTML = `
        <div class="deck-card-main">
          <div class="deck-card-title">${escapeHtml(deck.title)}</div>
          <div class="deck-card-meta">${deck.questions.length} questions · ${timeAgo(deck.createdAt)}</div>
        </div>
        <button class="icon-btn deck-delete" aria-label="Delete deck">🗑</button>
      `;
      card.querySelector(".deck-card-main").addEventListener("click", () => openDeckSummary(deck));
      card.querySelector(".deck-delete").addEventListener("click", (e) => {
        e.stopPropagation();
        if (confirm(`Delete "${deck.title}"?`)) {
          deleteDeck(deck.id);
          renderHome();
        }
      });
      list.appendChild(card);
    }
  }

  function openDeckSummary(deck) {
    currentDeck = deck;
    $("#summary-title").textContent = deck.title;
    $("#summary-count").textContent = deck.questions.length;
    showScreen("screen-summary");
  }

  $("#generate-btn").addEventListener("click", () => {
    const notes = $("#notes-input").value.trim();
    if (notes.split(/\s+/).length < 12) {
      toast("Add a bit more detail — a few full sentences works best.");
      return;
    }
    showScreen("screen-loading");
    setTimeout(() => {
      const { questions } = QuizGen.generateQuiz(notes, { maxQuestions: 25 });
      if (questions.length === 0) {
        toast("Couldn't find enough to quiz on — try longer, more specific notes.");
        showScreen("screen-home");
        return;
      }
      const titleInput = $("#deck-title").value.trim();
      const title = titleInput || autoTitle(notes);
      const deck = {
        id: "d" + Date.now() + Math.random().toString(36).slice(2, 7),
        title,
        notes,
        createdAt: Date.now(),
        questions,
        mastery: {},
      };
      upsertDeck(deck);
      $("#notes-input").value = "";
      $("#deck-title").value = "";
      openDeckSummary(deck);
    }, 250);
  });

  function autoTitle(notes) {
    const words = notes.split(/\s+/).slice(0, 6).join(" ");
    return words.length < notes.length ? words + "…" : words;
  }

  $all(".back-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      showScreen(btn.dataset.target);
      if (btn.dataset.target === "screen-home") renderHome();
    });
  });

  $("#summary-delete").addEventListener("click", () => {
    if (!currentDeck) return;
    if (confirm(`Delete "${currentDeck.title}"?`)) {
      deleteDeck(currentDeck.id);
      currentDeck = null;
      renderHome();
      showScreen("screen-home");
    }
  });

  // ---------------------------------------------------------------
  // FLASHCARDS
  // ---------------------------------------------------------------
  const flash = {
    order: [],
    index: 0,
    known: 0,
    learning: 0,
    flipped: false,
  };

  $("#start-flashcards").addEventListener("click", () => {
    if (!currentDeck) return;
    flash.order = currentDeck.questions.map((q) => q.id);
    shuffleArr(flash.order);
    flash.index = 0;
    flash.known = 0;
    flash.learning = 0;
    renderFlashCard();
    showScreen("screen-flash");
  });

  $("#flash-shuffle").addEventListener("click", () => {
    shuffleArr(flash.order);
    flash.index = 0;
    renderFlashCard();
    vibrate(10);
  });

  function currentFlashQuestion() {
    const id = flash.order[flash.index];
    return currentDeck.questions.find((q) => q.id === id);
  }

  function renderFlashCard() {
    if (flash.index >= flash.order.length) {
      finishFlashcards();
      return;
    }
    const q = currentFlashQuestion();
    const card = $("#flash-card");
    card.classList.remove("flipped", "fly-left", "fly-right");
    flash.flipped = false;
    $("#flash-tag").textContent = q.type === "define" ? "DEFINE" : "CLOZE";
    $("#flash-front-text").textContent = q.type === "define" ? q.prompt : q.prompt;
    $("#flash-answer-text").textContent = q.type === "define" ? q.answer : q.answer;
    $("#flash-context-text").textContent = q.type === "define" ? q.sourceSentence : `"${q.sourceSentence}"`;
    $("#flash-known-count").textContent = flash.known;
    $("#flash-learning-count").textContent = flash.learning;
    $("#flash-progress").style.width = Math.round((flash.index / flash.order.length) * 100) + "%";
  }

  function flipFlashCard() {
    flash.flipped = !flash.flipped;
    $("#flash-card").classList.toggle("flipped", flash.flipped);
    vibrate(8);
  }

  function gradeFlashCard(known) {
    const q = currentFlashQuestion();
    if (!currentDeck.mastery) currentDeck.mastery = {};
    currentDeck.mastery[q.id] = known ? "known" : "learning";
    if (known) flash.known++; else flash.learning++;
    upsertDeck(currentDeck);

    const card = $("#flash-card");
    card.classList.add(known ? "fly-right" : "fly-left");
    vibrate(known ? [10] : [10, 40, 10]);
    setTimeout(() => {
      flash.index++;
      renderFlashCard();
    }, 220);
  }

  $("#flash-stage").addEventListener("click", (e) => {
    if (e.target.closest(".flash-controls")) return;
    flipFlashCard();
  });
  $("#flash-yes").addEventListener("click", () => gradeFlashCard(true));
  $("#flash-no").addEventListener("click", () => gradeFlashCard(false));

  // Swipe gesture on the card
  (function setupSwipe() {
    const card = $("#flash-card");
    let startX = 0, startY = 0, dx = 0, dragging = false;

    card.addEventListener("pointerdown", (e) => {
      dragging = true;
      startX = e.clientX; startY = e.clientY; dx = 0;
      card.setPointerCapture(e.pointerId);
      card.style.transition = "none";
    });
    card.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (Math.abs(dx) > Math.abs(dy)) {
        card.style.transform = `translateX(${dx}px) rotate(${dx / 20}deg)`;
      }
    });
    function endDrag() {
      if (!dragging) return;
      dragging = false;
      card.style.transition = "";
      card.style.transform = "";
      if (Math.abs(dx) > 90) {
        gradeFlashCard(dx > 0);
      }
      dx = 0;
    }
    card.addEventListener("pointerup", endDrag);
    card.addEventListener("pointercancel", endDrag);
  })();

  function finishFlashcards() {
    const total = flash.known + flash.learning;
    $("#results-emoji").textContent = flash.known / Math.max(1, total) >= 0.7 ? "🎉" : "💪";
    $("#results-score").textContent = `${flash.known}/${total}`;
    $("#results-sub").textContent = `Marked known: ${flash.known} · still learning: ${flash.learning}`;
    $("#missed-wrap").classList.add("hidden");
    $("#results-retry-missed").classList.add("hidden");
    $("#results-retry").onclick = () => {
      flash.order = currentDeck.questions.map((q) => q.id);
      shuffleArr(flash.order);
      flash.index = 0; flash.known = 0; flash.learning = 0;
      renderFlashCard();
      showScreen("screen-flash");
    };
    $("#results-home").onclick = () => { renderHome(); showScreen("screen-home"); };
    showScreen("screen-results");
  }

  // ---------------------------------------------------------------
  // MULTIPLE CHOICE
  // ---------------------------------------------------------------
  const mcq = {
    order: [],
    index: 0,
    score: 0,
    missed: [],
    answered: false,
  };

  function startMcq(questions) {
    mcq.order = questions.slice();
    shuffleArr(mcq.order);
    mcq.index = 0;
    mcq.score = 0;
    mcq.missed = [];
    mcq.answered = false;
    $("#mcq-total").textContent = mcq.order.length;
    renderMcqQuestion();
    showScreen("screen-mcq");
  }

  $("#start-mcq").addEventListener("click", () => {
    if (!currentDeck) return;
    startMcq(currentDeck.questions);
  });

  function renderMcqQuestion() {
    if (mcq.index >= mcq.order.length) {
      finishMcq();
      return;
    }
    mcq.answered = false;
    const q = mcq.order[mcq.index];
    $("#mcq-question").textContent = q.type === "define" ? q.prompt : `Fill in the blank:\n${q.prompt}`;
    $("#mcq-score").textContent = mcq.score;
    $("#mcq-progress").style.width = Math.round((mcq.index / mcq.order.length) * 100) + "%";
    $("#mcq-next").classList.add("hidden");

    const optionsWrap = $("#mcq-options");
    optionsWrap.innerHTML = "";
    q.choices.forEach((choice) => {
      const btn = document.createElement("button");
      btn.className = "mcq-option";
      btn.textContent = choice;
      btn.addEventListener("click", () => selectMcqOption(btn, choice, q));
      optionsWrap.appendChild(btn);
    });
  }

  function selectMcqOption(btn, choice, q) {
    if (mcq.answered) return;
    mcq.answered = true;
    const correct = choice.toLowerCase() === q.answerShort.toLowerCase();
    $all(".mcq-option").forEach((b) => {
      b.disabled = true;
      if (b.textContent.toLowerCase() === q.answerShort.toLowerCase()) b.classList.add("correct");
    });
    if (!correct) {
      btn.classList.add("wrong");
      mcq.missed.push(q);
    } else {
      mcq.score++;
    }
    vibrate(correct ? 12 : [10, 40, 10]);
    $("#mcq-score").textContent = mcq.score;
    $("#mcq-next").classList.remove("hidden");
  }

  $("#mcq-next").addEventListener("click", () => {
    mcq.index++;
    renderMcqQuestion();
  });

  function finishMcq() {
    const total = mcq.order.length;
    const pct = total ? mcq.score / total : 0;
    $("#results-emoji").textContent = pct >= 0.8 ? "🎉" : pct >= 0.5 ? "👍" : "💪";
    $("#results-score").textContent = `${mcq.score}/${total}`;
    $("#results-sub").textContent = pct >= 0.8 ? "Excellent work!" : pct >= 0.5 ? "Good progress — keep going." : "Review and try again.";

    const missedWrap = $("#missed-wrap");
    const missedList = $("#missed-list");
    missedList.innerHTML = "";
    if (mcq.missed.length > 0) {
      missedWrap.classList.remove("hidden");
      mcq.missed.forEach((q) => {
        const item = document.createElement("div");
        item.className = "missed-item";
        item.innerHTML = `<div class="missed-q">${escapeHtml(q.type === "define" ? q.prompt : q.prompt)}</div>
                           <div class="missed-a">Answer: <b>${escapeHtml(q.answerShort)}</b></div>`;
        missedList.appendChild(item);
      });
      $("#results-retry-missed").classList.remove("hidden");
      $("#results-retry-missed").onclick = () => startMcq(mcq.missed);
    } else {
      missedWrap.classList.add("hidden");
      $("#results-retry-missed").classList.add("hidden");
    }

    $("#results-retry").onclick = () => startMcq(currentDeck.questions);
    $("#results-home").onclick = () => { renderHome(); showScreen("screen-home"); };
    showScreen("screen-results");
  }

  function shuffleArr(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  // ---------------------------------------------------------------
  // PWA install prompt
  // ---------------------------------------------------------------
  let deferredInstallPrompt = null;
  function isStandalone() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  }
  function isIos() {
    return /iphone|ipad|ipod/i.test(navigator.userAgent);
  }

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (!localStorage.getItem(DISMISS_KEY) && !isStandalone()) {
      $("#install-banner-text").textContent = "Install QuizIt for the full app experience.";
      $("#install-btn").classList.remove("hidden");
      $("#install-banner").classList.remove("hidden");
    }
  });

  $("#install-btn").addEventListener("click", async () => {
    if (!deferredInstallPrompt) return;
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice;
    deferredInstallPrompt = null;
    $("#install-banner").classList.add("hidden");
  });

  $("#install-dismiss").addEventListener("click", () => {
    $("#install-banner").classList.add("hidden");
    localStorage.setItem(DISMISS_KEY, "1");
  });

  document.addEventListener("DOMContentLoaded", () => {
    if (isIos() && !isStandalone() && !localStorage.getItem(DISMISS_KEY)) {
      $("#install-btn").classList.add("hidden");
      $("#install-banner-text").textContent = "Install: tap Share, then \"Add to Home Screen\".";
      $("#install-banner").classList.remove("hidden");
    }
  });

  // ---------------------------------------------------------------
  // Service worker
  // ---------------------------------------------------------------
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    });
  }

  // ---------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------
  renderHome();
})();
