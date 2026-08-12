window.Pomewdoro = (function () {
  function formatTime(totalSeconds) {
    var m = Math.floor(totalSeconds / 60);
    var s = Math.floor(totalSeconds % 60);
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function randomSprite(catTypes) {
    return catTypes[Math.floor(Math.random() * catTypes.length)];
  }

  function appendLandedCat(landedZone, sprite) {
    if (!landedZone) return;
    var img = document.createElement('img');
    img.src = '/static/named_cats/' + sprite + '.png';
    img.className = 'landed-cat';
    landedZone.appendChild(img);
  }

  function dropCat(skyZone, landedZone, catTypes) {
    if (!skyZone) return;
    var sprite = randomSprite(catTypes);
    var img = document.createElement('img');
    img.src = '/static/named_cats/' + sprite + '.png';
    img.className = 'falling-cat';
    img.style.left = (8 + Math.random() * 78) + '%';
    img.style.setProperty('--land-rot', (Math.random() * 16 - 8) + 'deg');
    img.style.animationDuration = (1.1 + Math.random() * 0.5) + 's';
    skyZone.appendChild(img);
    // once it lands, remove it from the falling layer and add it permanently to the tray
    img.addEventListener('animationend', function () {
      img.remove();
      appendLandedCat(landedZone, sprite);
    });
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
      var duration = 0.5 + Math.random() * 0.35; // under a second — that's the whole point
      p.style.animationDuration = duration + 's';
      p.style.animationDelay = (Math.random() * 0.25) + 's';

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
    var catTypes = opts.catTypes || [];
    var remaining = opts.remainingSeconds;
    var totalSeconds = opts.durationMinutes * 60;
    var lastDroppedMinute = Math.floor(opts.elapsedSeconds / 60);

    var readout = document.getElementById('pomo-readout');
    var skyZone = document.getElementById('pomo-sky');
    var landedZone = document.getElementById('pomo-landed');
    var trayCount = document.getElementById('pomo-tray-count');
    var finishForm = document.getElementById('pomo-finish-form');
    var growWrap = document.getElementById('growcat-wrap');
    var growCat = document.getElementById('growcat');
    var count = lastDroppedMinute;
    if (trayCount) trayCount.textContent = count;
    if (readout) readout.textContent = formatTime(remaining);

    // backfill the tray for minutes already earned before this page load (e.g. after a refresh)
    for (var i = 0; i < count; i++) {
      appendLandedCat(landedZone, randomSprite(catTypes));
    }

    var interval = setInterval(function () {
      remaining -= 1;
      if (readout) readout.textContent = formatTime(Math.max(0, remaining));

      var elapsedNow = totalSeconds - remaining;
      var currentMinute = Math.floor(elapsedNow / 60);
      if (opts.phase === 'focus' && currentMinute > lastDroppedMinute && currentMinute <= opts.durationMinutes) {
        lastDroppedMinute = currentMinute;
        count += 1;
        if (trayCount) trayCount.textContent = count;
        dropCat(skyZone, landedZone, catTypes);
      }

      if (remaining <= 0) {
        clearInterval(interval);
        // the growth/burst payoff is exclusive to reaching 0:00 naturally —
        // an early "Stop & Collect" submits pomo-finish-form directly and never hits this branch
        if (opts.phase === 'focus' && growWrap && growCat) {
          var heartsInput = document.getElementById('pomo-hearts-caught');
          triggerBurst(growWrap, growCat, function (caught) {
            if (heartsInput) heartsInput.value = caught;
          });
          setTimeout(function () {
            if (finishForm) finishForm.submit();
          }, 1500);
        } else if (finishForm) {
          finishForm.submit();
        }
      }
    }, 1000);
  }

  return { initTimer: initTimer };
})();
