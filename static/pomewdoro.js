window.Pomewdoro = (function () {
  function formatTime(totalSeconds) {
    var m = Math.floor(totalSeconds / 60);
    var s = Math.floor(totalSeconds % 60);
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function setPlayState(container, selector, state) {
    if (!container) return;
    var els = container.querySelectorAll(selector);
    for (var i = 0; i < els.length; i++) {
      els[i].style.animationPlayState = state;
    }
  }

  var BURST_PARTICLE_COUNT = 18;

  function triggerBurst(wrapEl, catEl, onCatch) {
    if (!wrapEl || !catEl) return;
    catEl.classList.add('bursting');

    var counter = document.createElement('div');
    counter.className = 'heart-catch-counter';
    counter.id = 'heart-catch-counter';
    counter.textContent = '❤️ 0';
    wrapEl.appendChild(counter);
    var caught = 0;

    var symbols = ['❤️', '💗', '✨', '⭐', '💛', '💜', '🩷'];
    for (var i = 0; i < BURST_PARTICLE_COUNT; i++) {
      var p = document.createElement('span');
      p.className = 'burst-particle';
      p.textContent = symbols[Math.floor(Math.random() * symbols.length)];
      var angle = Math.random() * Math.PI - Math.PI; // spray upward/outward
      var dist = 70 + Math.random() * 110;
      p.style.setProperty('--fx', Math.cos(angle) * dist + 'px');
      p.style.setProperty('--fy', (Math.sin(angle) * dist - 40) + 'px');
      // fast pop-out, then a long gentle continued drift (see burst-fly keyframes)
      // so it's exciting to arrive but stays catchable for several seconds
      var duration = 4.2 + Math.random() * 0.4;
      p.style.animationDuration = duration + 's';
      p.style.animationDelay = (Math.random() * 0.8) + 's'; // staggered, not a single overwhelming clump
      if (document.hidden) p.style.animationPlayState = 'paused';

      (function (particle) {
        particle.addEventListener('click', function () {
          if (particle.classList.contains('caught')) return;
          particle.classList.add('caught');
          caught += 1;
          counter.textContent = '❤️ ' + caught;
          if (onCatch) onCatch(caught);
        });
        particle.addEventListener('animationend', function (e) {
          if (e.animationName !== 'burst-caught') particle.remove();
        });
      })(p);

      wrapEl.appendChild(p);
    }
  }

  function initTimer(opts) {
    var remaining = opts.remainingSeconds;
    var totalSeconds = opts.durationMinutes * 60;

    var readout = document.getElementById('pomo-readout');
    var finishForm = document.getElementById('pomo-finish-form');
    var growWrap = document.getElementById('growcat-wrap');
    var growCat = document.getElementById('growcat');
    var pausedBadge = document.getElementById('pomo-paused-badge');
    if (readout) readout.textContent = formatTime(remaining);

    // --- pause everything the moment the tab is hidden; tell the server how ---
    // long we were away so its own elapsed-time math (what actually banks
    // hearts and detects completion) excludes that time too, not just the display
    var hiddenAt = null;

    function reportPause(seconds) {
      if (seconds <= 0) return;
      var body = 'paused_seconds=' + encodeURIComponent(seconds);
      if (navigator.sendBeacon) {
        navigator.sendBeacon(
          '/pomewdoro/pause-adjust',
          new Blob([body], { type: 'application/x-www-form-urlencoded' })
        );
      } else {
        fetch('/pomewdoro/pause-adjust', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: body,
          keepalive: true
        });
      }
    }

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        hiddenAt = Date.now();
        if (growCat) growCat.style.animationPlayState = 'paused';
        setPlayState(growWrap, '.burst-particle', 'paused');
        if (pausedBadge) pausedBadge.hidden = false;
      } else if (hiddenAt) {
        var pausedSeconds = Math.round((Date.now() - hiddenAt) / 1000);
        hiddenAt = null;
        reportPause(pausedSeconds);
        if (growCat) growCat.style.animationPlayState = 'running';
        setPlayState(growWrap, '.burst-particle', 'running');
        if (pausedBadge) pausedBadge.hidden = true;
      }
    });

    var interval = setInterval(function () {
      if (document.hidden) return; // frozen — resumes exactly where it left off once visible again

      remaining -= 1;
      if (readout) readout.textContent = formatTime(Math.max(0, remaining));

      if (remaining <= 0) {
        clearInterval(interval);
        // the growth/burst payoff is exclusive to reaching 0:00 naturally —
        // an early "Give Up" submits pomo-finish-form directly and never hits this branch
        if (opts.phase === 'focus' && growWrap && growCat) {
          var heartsInput = document.getElementById('pomo-hearts-caught');
          triggerBurst(growWrap, growCat, function (caught) {
            if (heartsInput) heartsInput.value = caught;
          });
          setTimeout(function () {
            if (finishForm) finishForm.submit();
          }, 5800); // covers the full staggered burst window (up to ~0.8s delay + ~4.6s flight)
        } else if (finishForm) {
          finishForm.submit();
        }
      }
    }, 1000);
  }

  return { initTimer: initTimer };
})();
