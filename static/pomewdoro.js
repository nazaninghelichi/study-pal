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
        if (finishForm) finishForm.submit();
      }
    }, 1000);
  }

  return { initTimer: initTimer };
})();
