(function () {
  "use strict";

  const STORAGE_KEY = "quizit.decks.v1";
  const PROFILE_KEY = "quizit.profile.v1";
  const DISMISS_KEY = "quizit.installDismissed";

  const XP_KNOWN = 8;
  const XP_LEARNING = 2;
  const XP_CORRECT = 10;
  const XP_INCORRECT = 2;
  const XP_SESSION_BONUS = 20;
  const XP_STREAK_BONUS = 15;
  const STREAK_MILESTONES = [3, 7, 14, 30, 50, 100];

  const BADGES = [
    { id: "first_deck", icon: "📚", label: "First Deck", check: (p, decks) => decks.length >= 1 },
    { id: "streak_3", icon: "🔥", label: "3-Day Streak", check: (p) => (p.streak.best || 0) >= 3 },
    { id: "streak_7", icon: "⚡", label: "7-Day Streak", check: (p) => (p.streak.best || 0) >= 7 },
    { id: "cards_50", icon: "🎯", label: "50 Mastered", check: (p, decks) => totalKnownAcrossDecks(decks) >= 50 },
    { id: "quiz_ace", icon: "🏆", label: "Perfect Quiz", check: (p) => !!(p.flags && p.flags.perfectQuiz) },
  ];

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
  function todayStr() {
    const d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function daysBetween(a, b) {
    return Math.round((new Date(b + "T00:00:00") - new Date(a + "T00:00:00")) / 86400000);
  }
  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
  }

  function showScreen(id) {
    $all(".screen").forEach((s) => s.classList.toggle("active", s.id === id));
    window.scrollTo(0, 0);
  }

  function shuffleArr(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  // ---------------------------------------------------------------
  // Profile (XP, level, streak, badges, reminder)
  // ---------------------------------------------------------------
  function loadProfile() {
    let p;
    try { p = JSON.parse(localStorage.getItem(PROFILE_KEY)); } catch (e) { p = null; }
    if (!p) p = {};
    p.xp = p.xp || 0;
    p.streak = p.streak || { count: 0, best: 0, lastStudyDate: null };
    p.badges = p.badges || {};
    p.reminder = p.reminder || { enabled: false, lastNotifiedDate: null };
    p.flags = p.flags || {};
    return p;
  }
  function saveProfile(p) { localStorage.setItem(PROFILE_KEY, JSON.stringify(p)); }
  let profile = loadProfile();

  function levelInfo(xp) {
    let level = 1, needed = 100, total = 0;
    while (xp >= total + needed) {
      total += needed;
      level++;
      needed = Math.round(needed * 1.35);
    }
    return { level, into: xp - total, needed, total };
  }

  function addXp(amount) {
    const before = levelInfo(profile.xp).level;
    profile.xp += amount;
    const after = levelInfo(profile.xp);
    saveProfile(profile);
    renderHeaderChips();
    return { amount, leveledUp: after.level > before, newLevel: after.level };
  }

  function renderHeaderChips() {
    $("#streak-count").textContent = profile.streak.count || 0;
    $("#streak-chip").classList.toggle("lit", (profile.streak.count || 0) > 0);
    const li = levelInfo(profile.xp);
    $("#level-num").textContent = li.level;
    $("#xp-fill").style.width = Math.round((li.into / li.needed) * 100) + "%";
    $("#xp-label").innerHTML = `${li.into} / ${li.needed} to next level`;
  }

  function touchStreak() {
    const today = todayStr();
    if (profile.streak.lastStudyDate === today) return;
    let newCount;
    if (profile.streak.lastStudyDate && daysBetween(profile.streak.lastStudyDate, today) === 1) {
      newCount = (profile.streak.count || 0) + 1;
    } else {
      newCount = 1;
    }
    profile.streak.count = newCount;
    profile.streak.best = Math.max(profile.streak.best || 0, newCount);
    profile.streak.lastStudyDate = today;
    saveProfile(profile);
    renderHeaderChips();
    addXp(XP_STREAK_BONUS);
    if (newCount > 1) {
      toast(`🔥 ${newCount}-day streak!`);
      if (STREAK_MILESTONES.includes(newCount)) {
        burstConfetti();
        vibrate([15, 40, 15, 40, 15]);
      }
    }
    checkBadges();
  }

  function totalKnownAcrossDecks(decks) {
    let count = 0;
    for (const deck of decks) {
      for (const qid in deck.mastery) {
        const m = deck.mastery[qid];
        if (m && m.state === "known") count++;
      }
    }
    return count;
  }

  function checkBadges() {
    const decks = loadDecks();
    const newly = [];
    for (const b of BADGES) {
      if (!profile.badges[b.id] && b.check(profile, decks)) {
        profile.badges[b.id] = Date.now();
        newly.push(b);
      }
    }
    if (newly.length) saveProfile(profile);
    renderBadgesRow();
    return newly;
  }

  function renderBadgesRow() {
    const decks = loadDecks();
    const earnedAny = Object.keys(profile.badges).length > 0;
    const wrap = $("#badges-wrap");
    if (decks.length === 0 && !earnedAny) { wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    const row = $("#badges-row");
    row.innerHTML = "";
    for (const b of BADGES) {
      const earned = !!profile.badges[b.id];
      const el = document.createElement("div");
      el.className = "badge" + (earned ? " earned" : "");
      el.title = earned ? `${b.label} — earned ${new Date(profile.badges[b.id]).toLocaleDateString()}` : `${b.label} — locked`;
      el.innerHTML = `<div class="badge-icon">${b.icon}</div><div class="badge-label">${b.label}</div>`;
      row.appendChild(el);
    }
  }

  // ---------------------------------------------------------------
  // Storage: decks + spaced-repetition mastery
  // ---------------------------------------------------------------
  function migrateDeckMastery(deck) {
    if (!deck.mastery) deck.mastery = {};
    for (const qid in deck.mastery) {
      const m = deck.mastery[qid];
      if (typeof m === "string") {
        deck.mastery[qid] = m === "known"
          ? { state: "known", reps: 1, interval: 1, dueAt: Date.now() + 86400000 }
          : { state: "learning", reps: 0, interval: 0, dueAt: Date.now() };
      }
    }
    return deck;
  }
  function loadDecks() {
    let decks;
    try { decks = JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; } catch (e) { decks = []; }
    decks.forEach(migrateDeckMastery);
    return decks;
  }
  function saveDecks(decks) { localStorage.setItem(STORAGE_KEY, JSON.stringify(decks)); }
  function upsertDeck(deck) {
    const decks = loadDecks();
    const idx = decks.findIndex((d) => d.id === deck.id);
    if (idx >= 0) decks[idx] = deck; else decks.unshift(deck);
    saveDecks(decks);
  }
  function deleteDeck(id) { saveDecks(loadDecks().filter((d) => d.id !== id)); }

  function getMastery(deck, qid) {
    const m = deck.mastery[qid];
    if (!m) return null;
    if (typeof m === "string") return { state: m, reps: m === "known" ? 1 : 0, interval: m === "known" ? 1 : 0, dueAt: 0 };
    return m;
  }
  function isDue(deck, qid) {
    const m = getMastery(deck, qid);
    if (!m) return true;
    return (m.dueAt || 0) <= Date.now();
  }
  function dueCount(deck) { return deck.questions.filter((q) => isDue(deck, q.id)).length; }
  function deckMasteryPct(deck) {
    const total = deck.questions.length;
    if (!total) return 0;
    const known = deck.questions.filter((q) => { const m = getMastery(deck, q.id); return m && m.state === "known"; }).length;
    return Math.round((known / total) * 100);
  }

  function gradeQuestion(deck, qid, correct) {
    const prev = getMastery(deck, qid) || { state: "new", reps: 0, interval: 0, dueAt: 0 };
    let rec;
    if (correct) {
      const reps = prev.reps + 1;
      let interval;
      if (reps === 1) interval = 1;
      else if (reps === 2) interval = 3;
      else interval = Math.max(1, Math.round((prev.interval || 3) * 2.2));
      rec = { state: "known", reps, interval, dueAt: Date.now() + interval * 86400000 };
    } else {
      rec = { state: "learning", reps: 0, interval: 0, dueAt: Date.now() };
    }
    deck.mastery[qid] = rec;
    return rec;
  }

  // ---------------------------------------------------------------
  // App state
  // ---------------------------------------------------------------
  let currentDeck = null;

  // ---------------------------------------------------------------
  // HOME
  // ---------------------------------------------------------------
  function renderHome() {
    renderHeaderChips();
    renderBadgesRow();
    renderReminderUI();
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
      const due = dueCount(deck);
      const masteryPct = deckMasteryPct(deck);
      const card = document.createElement("div");
      card.className = "deck-card";
      card.innerHTML = `
        <div class="deck-card-main">
          <div class="deck-card-title-row">
            <div class="deck-card-title">${escapeHtml(deck.title)}</div>
            ${due > 0 ? `<span class="due-pill">${due} due</span>` : ""}
          </div>
          <div class="deck-card-meta">${deck.questions.length} questions · ${timeAgo(deck.createdAt)}</div>
          <div class="deck-mastery-track"><div class="deck-mastery-fill" style="width:${masteryPct}%;"></div></div>
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
    const due = dueCount(deck);
    const callout = $("#due-callout");
    if (due > 0) {
      callout.textContent = `${due} card${due === 1 ? "" : "s"} due for review`;
      callout.className = "due-callout has-due";
      $("#start-smart-review").textContent = `🔥 Smart Review (${due})`;
    } else {
      callout.textContent = "All caught up ✅";
      callout.className = "due-callout all-caught";
      $("#start-smart-review").textContent = "🔥 Review all";
    }
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
      checkBadges();
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
    order: [], index: 0, known: 0, learning: 0, flipped: false,
    sourceQuestions: [], sessionXp: 0, leveledUp: false, newLevel: null,
  };

  function beginFlashcardsSession(questions) {
    flash.sourceQuestions = questions;
    flash.order = questions.map((q) => q.id);
    shuffleArr(flash.order);
    flash.index = 0;
    flash.known = 0;
    flash.learning = 0;
    flash.sessionXp = 0;
    flash.leveledUp = false;
    flash.newLevel = null;
    renderFlashCard();
    showScreen("screen-flash");
  }

  $("#start-flashcards").addEventListener("click", () => {
    if (!currentDeck) return;
    beginFlashcardsSession(currentDeck.questions);
  });

  $("#start-smart-review").addEventListener("click", () => {
    if (!currentDeck) return;
    const due = currentDeck.questions.filter((q) => isDue(currentDeck, q.id));
    beginFlashcardsSession(due.length ? due : currentDeck.questions);
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
    $("#flash-front-text").textContent = q.prompt;
    $("#flash-answer-text").textContent = q.answer;
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
    gradeQuestion(currentDeck, q.id, known);
    upsertDeck(currentDeck);
    if (known) flash.known++; else flash.learning++;

    const xpGain = known ? XP_KNOWN : XP_LEARNING;
    const xpResult = addXp(xpGain);
    flash.sessionXp += xpGain;
    if (xpResult.leveledUp) { flash.leveledUp = true; flash.newLevel = xpResult.newLevel; }
    touchStreak();

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
      if (Math.abs(dx) > 90) gradeFlashCard(dx > 0);
      dx = 0;
    }
    card.addEventListener("pointerup", endDrag);
    card.addEventListener("pointercancel", endDrag);
  })();

  function buildExtrasHtml(sessionXp, leveledUp, newLevel, newlyBadges) {
    const lines = [`<div class="result-line xp">+${sessionXp} XP earned</div>`];
    if (leveledUp) lines.push(`<div class="result-line levelup">🎉 Level up! Now level ${newLevel}</div>`);
    newlyBadges.forEach((b) => lines.push(`<div class="result-line badge">🏅 Badge unlocked: ${b.label}</div>`));
    return lines.join("");
  }

  function finishFlashcards() {
    const total = flash.known + flash.learning;
    const bonus = addXp(XP_SESSION_BONUS);
    flash.sessionXp += XP_SESSION_BONUS;
    if (bonus.leveledUp) { flash.leveledUp = true; flash.newLevel = bonus.newLevel; }
    const newlyBadges = checkBadges();

    $("#results-emoji").textContent = flash.known / Math.max(1, total) >= 0.7 ? "🎉" : "💪";
    $("#results-score").textContent = `${flash.known}/${total}`;
    $("#results-sub").textContent = `Marked known: ${flash.known} · still learning: ${flash.learning}`;
    $("#results-extras").innerHTML = buildExtrasHtml(flash.sessionXp, flash.leveledUp, flash.newLevel, newlyBadges);
    $("#missed-wrap").classList.add("hidden");
    $("#results-retry-missed").classList.add("hidden");

    if (flash.leveledUp || newlyBadges.length) { burstConfetti(); vibrate([15, 40, 15]); }

    $("#results-retry").onclick = () => beginFlashcardsSession(flash.sourceQuestions);
    $("#results-home").onclick = () => { renderHome(); showScreen("screen-home"); };
    showScreen("screen-results");
  }

  // ---------------------------------------------------------------
  // MULTIPLE CHOICE
  // ---------------------------------------------------------------
  const mcq = {
    order: [], index: 0, score: 0, missed: [], answered: false,
    sessionXp: 0, leveledUp: false, newLevel: null,
  };

  function startMcq(questions) {
    mcq.order = questions.slice();
    shuffleArr(mcq.order);
    mcq.index = 0;
    mcq.score = 0;
    mcq.missed = [];
    mcq.answered = false;
    mcq.sessionXp = 0;
    mcq.leveledUp = false;
    mcq.newLevel = null;
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

    if (currentDeck) gradeQuestion(currentDeck, q.id, correct);
    if (currentDeck) upsertDeck(currentDeck);

    if (!correct) {
      btn.classList.add("wrong");
      mcq.missed.push(q);
    } else {
      mcq.score++;
    }

    const xpGain = correct ? XP_CORRECT : XP_INCORRECT;
    const xpResult = addXp(xpGain);
    mcq.sessionXp += xpGain;
    if (xpResult.leveledUp) { mcq.leveledUp = true; mcq.newLevel = xpResult.newLevel; }
    touchStreak();

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
    const bonus = addXp(XP_SESSION_BONUS);
    mcq.sessionXp += XP_SESSION_BONUS;
    if (bonus.leveledUp) { mcq.leveledUp = true; mcq.newLevel = bonus.newLevel; }

    if (total >= 4 && pct === 1 && !profile.flags.perfectQuiz) {
      profile.flags.perfectQuiz = true;
      saveProfile(profile);
    }
    const newlyBadges = checkBadges();

    $("#results-emoji").textContent = pct >= 0.8 ? "🎉" : pct >= 0.5 ? "👍" : "💪";
    $("#results-score").textContent = `${mcq.score}/${total}`;
    $("#results-sub").textContent = pct >= 0.8 ? "Excellent work!" : pct >= 0.5 ? "Good progress — keep going." : "Review and try again.";
    $("#results-extras").innerHTML = buildExtrasHtml(mcq.sessionXp, mcq.leveledUp, mcq.newLevel, newlyBadges);

    const missedWrap = $("#missed-wrap");
    const missedList = $("#missed-list");
    missedList.innerHTML = "";
    if (mcq.missed.length > 0) {
      missedWrap.classList.remove("hidden");
      mcq.missed.forEach((q) => {
        const item = document.createElement("div");
        item.className = "missed-item";
        item.innerHTML = `<div class="missed-q">${escapeHtml(q.prompt)}</div>
                           <div class="missed-a">Answer: <b>${escapeHtml(q.answerShort)}</b></div>`;
        missedList.appendChild(item);
      });
      $("#results-retry-missed").classList.remove("hidden");
      $("#results-retry-missed").onclick = () => startMcq(mcq.missed);
    } else {
      missedWrap.classList.add("hidden");
      $("#results-retry-missed").classList.add("hidden");
    }

    if (pct === 1 || mcq.leveledUp || newlyBadges.length) { burstConfetti(); vibrate([15, 40, 15]); }

    $("#results-retry").onclick = () => startMcq(currentDeck.questions);
    $("#results-home").onclick = () => { renderHome(); showScreen("screen-home"); };
    showScreen("screen-results");
  }

  // ---------------------------------------------------------------
  // Celebration: lightweight canvas confetti
  // ---------------------------------------------------------------
  let confettiParticles = [];
  let confettiRunning = false;
  function resizeConfettiCanvas() {
    const c = $("#confetti-canvas");
    c.width = window.innerWidth * (window.devicePixelRatio || 1);
    c.height = window.innerHeight * (window.devicePixelRatio || 1);
  }
  window.addEventListener("resize", resizeConfettiCanvas);

  function burstConfetti(count) {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const canvas = $("#confetti-canvas");
    if (!canvas.width) resizeConfettiCanvas();
    const dpr = window.devicePixelRatio || 1;
    const colors = ["#ff7a45", "#ffb648", "#3fe0b0", "#ff5c72", "#ffd97a"];
    const cx = canvas.width / 2;
    const n = count || 48;
    for (let i = 0; i < n; i++) {
      const angle = Math.random() * Math.PI + Math.PI;
      const speed = (2 + Math.random() * 4) * dpr;
      confettiParticles.push({
        x: cx + (Math.random() - 0.5) * 100 * dpr,
        y: canvas.height * 0.35,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 4 * dpr,
        size: (4 + Math.random() * 4) * dpr,
        color: colors[Math.floor(Math.random() * colors.length)],
        rot: Math.random() * Math.PI * 2,
        vrot: (Math.random() - 0.5) * 0.3,
        life: 0,
        maxLife: 70 + Math.random() * 30,
      });
    }
    if (!confettiRunning) { confettiRunning = true; requestAnimationFrame(stepConfetti); }
  }

  function stepConfetti() {
    const canvas = $("#confetti-canvas");
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const gravity = 0.18 * (window.devicePixelRatio || 1);
    confettiParticles = confettiParticles.filter((p) => p.life < p.maxLife);
    for (const p of confettiParticles) {
      p.vy += gravity;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vrot;
      p.life++;
      const alpha = Math.max(0, 1 - p.life / p.maxLife);
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      ctx.restore();
    }
    if (confettiParticles.length > 0) {
      requestAnimationFrame(stepConfetti);
    } else {
      confettiRunning = false;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }

  // ---------------------------------------------------------------
  // Study reminder (best-effort local notification, no push backend)
  // ---------------------------------------------------------------
  function renderReminderUI() {
    const sw = $("#reminder-switch");
    const on = !!profile.reminder.enabled;
    sw.classList.toggle("on", on);
    sw.setAttribute("aria-checked", String(on));
  }

  function toggleReminder() {
    if (!profile.reminder.enabled) {
      if (!("Notification" in window)) {
        toast("Notifications aren't supported in this browser.");
        return;
      }
      Notification.requestPermission().then((perm) => {
        if (perm === "granted") {
          profile.reminder.enabled = true;
          saveProfile(profile);
          renderReminderUI();
          toast("Reminder on — I'll nudge you if you haven't studied yet.");
        } else {
          toast("Notification permission blocked — enable it in your browser settings to use reminders.");
        }
      });
    } else {
      profile.reminder.enabled = false;
      saveProfile(profile);
      renderReminderUI();
    }
  }
  $("#reminder-switch").addEventListener("click", toggleReminder);
  $("#reminder-switch").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleReminder(); }
  });

  function maybeSendStreakReminder() {
    if (!profile.reminder.enabled) return;
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const today = todayStr();
    if (profile.streak.lastStudyDate === today) return;
    if (profile.reminder.lastNotifiedDate === today) return;
    if (new Date().getHours() < 12) return;
    try {
      new Notification("Keep your streak alive 🔥", {
        body: profile.streak.count > 0
          ? `You're on a ${profile.streak.count}-day streak — study a few cards before it resets.`
          : "You haven't studied today yet — a quick review only takes a minute.",
        icon: "icons/icon-192.png",
      });
    } catch (e) {}
    profile.reminder.lastNotifiedDate = today;
    saveProfile(profile);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") maybeSendStreakReminder();
  });

  // ---------------------------------------------------------------
  // PWA install prompt
  // ---------------------------------------------------------------
  let deferredInstallPrompt = null;
  function isStandalone() {
    return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  }
  function isIos() { return /iphone|ipad|ipod/i.test(navigator.userAgent); }

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
    maybeSendStreakReminder();
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
  resizeConfettiCanvas();
  renderHome();
})();
