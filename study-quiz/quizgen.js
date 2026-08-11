/*
 * QuizGen — turns raw notes text into flashcards / multiple-choice
 * questions entirely client-side (no network, no API keys).
 *
 * Approach: split into sentences, score candidate keywords per sentence
 * (frequency across the doc, length, capitalization, numerals), detect
 * "X is/means/refers to Y" definition sentences for higher-quality
 * question/answer pairs, and fall back to cloze deletion (blank the
 * best keyword) for everything else. Distractors for multiple choice
 * are pulled from other keywords/terms found in the same notes.
 */
(function (global) {
  const STOPWORDS = new Set(
    ("a about above after again against all am an and any are aren't as at be because been " +
     "before being below between both but by can't cannot could couldn't did didn't do does " +
     "doesn't doing don't down during each few for from further had hadn't has hasn't have " +
     "haven't having he he'd he'll he's her here here's hers herself him himself his how how's " +
     "i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my " +
     "myself no nor not of off on once only or other ought our ours ourselves out over own same " +
     "shan't she she'd she'll she's should shouldn't so some such than that that's the their " +
     "theirs them themselves then there there's these they they'd they'll they're they've this " +
     "those through to too under until up very was wasn't we we'd we'll we're we've were weren't " +
     "what what's when when's where where's which while who who's whom why why's with won't " +
     "would wouldn't you you'd you'll you're you've your yours yourself yourselves also often " +
     "however thus therefore may might one two three within across upon many much several like " +
     "使用 include includes including used uses using make makes making etc").split(" ")
  );

  const ABBREVS = new Set(["mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc", "e.g", "i.e", "st", "no", "fig", "approx"]);

  function splitSentences(text) {
    const clean = text.replace(/\s+/g, " ").trim();
    if (!clean) return [];
    const raw = clean.split(/(?<=[.!?])\s+(?=[A-Z0-9"'“])/g);
    const out = [];
    for (let s of raw) {
      s = s.trim();
      if (!s) continue;
      const lastWord = s.slice(0, -1).split(" ").pop().toLowerCase().replace(/\./g, "");
      if (ABBREVS.has(lastWord) && s.length < 400) {
        if (out.length) { out[out.length - 1] += " " + s; continue; }
      }
      out.push(s);
    }
    return out;
  }

  function tokenize(sentence) {
    return sentence.match(/[A-Za-z][A-Za-z'’-]*|\d+(\.\d+)?%?/g) || [];
  }

  function isCapitalizedWord(word) {
    return /^[A-Z][a-z'’-]+$/.test(word);
  }

  function buildFrequency(sentences) {
    const freq = new Map();
    for (const s of sentences) {
      const seen = new Set();
      for (const w of tokenize(s)) {
        const lw = w.toLowerCase();
        if (seen.has(lw)) continue;
        seen.add(lw);
        freq.set(lw, (freq.get(lw) || 0) + 1);
      }
    }
    return freq;
  }

  function keywordScore(word, index, wordsInSentence, freq, sentenceCount) {
    const lw = word.toLowerCase();
    if (STOPWORDS.has(lw)) return -1;
    if (/^\d+$/.test(word) && word.length <= 1) return -1;
    if (word.length < 3 && !/^\d+$/.test(word)) return -1;

    let score = 0;
    const f = freq.get(lw) || 1;
    // Reward terms that recur (central concepts) but punish ones that are
    // in almost every sentence (too generic to be an interesting blank).
    const docRatio = f / Math.max(1, sentenceCount);
    if (f >= 2 && docRatio < 0.6) score += 3;
    if (f === 1) score += 1;
    if (docRatio > 0.75) score -= 3;

    score += Math.min(word.length / 4, 3);
    if (isCapitalizedWord(word) && index > 0) score += 2.5;
    if (/^\d/.test(word)) score += 2;
    if (/^[A-Z]{2,}$/.test(word)) score += 2; // acronym

    return score;
  }

  function pickKeywords(sentence, freq, sentenceCount, max) {
    const words = tokenize(sentence);
    const scored = words.map((w, i) => ({
      word: w,
      index: i,
      score: keywordScore(w, i, words, freq, sentenceCount),
    })).filter(x => x.score > 0);
    scored.sort((a, b) => b.score - a.score);
    const chosen = [];
    const usedLower = new Set();
    for (const cand of scored) {
      if (chosen.length >= max) break;
      const lw = cand.word.toLowerCase();
      if (usedLower.has(lw)) continue;
      usedLower.add(lw);
      chosen.push(cand);
    }
    return chosen;
  }

  const DEFINITION_RE = /^(.{2,40}?)\s+(?:is|are|refers to|means|represents|denotes|is defined as|was|were)\s+(.{8,})[.]?$/i;

  function tryDefinitionPair(sentence) {
    const m = sentence.match(DEFINITION_RE);
    if (!m) return null;
    let term = m[1].trim().replace(/^(the|a|an)\s+/i, "");
    let def = m[2].trim().replace(/[.\s]+$/, "");
    const termWords = term.split(/\s+/);
    if (termWords.length > 6 || term.length < 2) return null;
    if (def.split(/\s+/).length < 2) return null;
    return { term, definition: def };
  }

  function cloze(sentence, keyword) {
    const re = new RegExp("\\b" + keyword.word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\b");
    const blanked = sentence.replace(re, "▁▁▁▁▁");
    if (blanked === sentence) return null;
    return blanked;
  }

  function levenshteinClose(a, b) {
    // cheap similarity heuristic for plausible distractors: similar length
    return Math.abs(a.length - b.length) <= 4;
  }

  function buildDistractors(correctAnswer, pool, count) {
    const correctLower = correctAnswer.toLowerCase();
    let candidates = pool.filter(
      (w) => w.toLowerCase() !== correctLower && levenshteinClose(w, correctAnswer)
    );
    if (candidates.length < count) {
      candidates = pool.filter((w) => w.toLowerCase() !== correctLower);
    }
    // de-dupe case-insensitively, shuffle
    const seen = new Set();
    const uniq = [];
    for (const c of candidates) {
      const lc = c.toLowerCase();
      if (seen.has(lc)) continue;
      seen.add(lc);
      uniq.push(c);
    }
    shuffle(uniq);
    return uniq.slice(0, count);
  }

  function shuffle(arr) {
    for (let i = arr.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [arr[i], arr[j]] = [arr[j], arr[i]];
    }
    return arr;
  }

  function generateQuiz(text, opts) {
    opts = opts || {};
    const maxQuestions = opts.maxQuestions || 25;
    const sentences = splitSentences(text).filter((s) => {
      const wc = s.split(/\s+/).length;
      return wc >= 5 && wc <= 45;
    });
    if (sentences.length === 0) return { questions: [], keywordPool: [] };

    const freq = buildFrequency(sentences);
    const sentenceCount = sentences.length;

    // Build a global pool of "interesting" terms/keywords for distractors.
    const poolSet = new Set();
    for (const s of sentences) {
      const words = tokenize(s);
      words.forEach((w, i) => {
        if (keywordScore(w, i, words, freq, sentenceCount) >= 2) poolSet.add(w);
      });
    }
    const keywordPool = Array.from(poolSet);

    const questions = [];
    const usedSentences = new Set();

    for (const sentence of sentences) {
      if (questions.length >= maxQuestions) break;
      if (usedSentences.has(sentence)) continue;

      const defPair = tryDefinitionPair(sentence);
      if (defPair) {
        const distractorPool = keywordPool.filter(
          (w) => w.toLowerCase() !== defPair.term.toLowerCase()
        );
        // also pull other terms captured from definition pairs so MCQ options
        // read like real answers rather than random nouns
        questions.push({
          id: "q" + questions.length,
          type: "define",
          prompt: `What is ${defPair.term}?`,
          answer: defPair.definition,
          answerShort: defPair.term,
          choicesSource: "sentence-defs",
          sourceSentence: sentence,
        });
        usedSentences.add(sentence);
        continue;
      }

      const picks = pickKeywords(sentence, freq, sentenceCount, 1);
      if (picks.length === 0) continue;
      const keyword = picks[0];
      const blanked = cloze(sentence, keyword);
      if (!blanked) continue;

      questions.push({
        id: "q" + questions.length,
        type: "cloze",
        prompt: blanked,
        answer: keyword.word,
        answerShort: keyword.word,
        sourceSentence: sentence,
      });
      usedSentences.add(sentence);
    }

    // Now assign MCQ choices for every question using the keyword pool
    // (and other questions' definition terms as extra distractor material).
    const defTerms = questions.filter(q => q.type === "define").map(q => q.answerShort);
    for (const q of questions) {
      let pool = q.type === "define"
        ? defTerms.concat(keywordPool)
        : keywordPool;
      const distractors = buildDistractors(q.answerShort, pool, 3);
      // Pad with generic filler if the notes were too short for 3 distractors.
      while (distractors.length < 3) {
        distractors.push("None of the above" + (distractors.length ? " (" + (distractors.length + 1) + ")" : ""));
      }
      const choices = shuffle([q.answerShort, ...distractors.slice(0, 3)]);
      q.choices = choices;
    }

    shuffle(questions);
    questions.forEach((q, i) => (q.order = i));

    return { questions, keywordPool };
  }

  global.QuizGen = { generateQuiz, splitSentences };
})(window);
