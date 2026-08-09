window.Pomewdoro = (function () {
  var CAT_TYPES = ['sleepy', 'pacing', 'goal', 'berserk', 'nudge'];

  function formatTime(totalSeconds) {
    var m = Math.floor(totalSeconds / 60);
    var s = Math.floor(totalSeconds % 60);
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  function dropCat(skyZone) {
    if (!skyZone) return;
    var sprite = CAT_TYPES[Math.floor(Math.random() * CAT_TYPES.length)];
    var img = document.createElement('img');
    img.src = '/static/cats/' + sprite + '.png';
    img.className = 'falling-cat';
    img.style.left = (8 + Math.random() * 78) + '%';
    img.style.animationDuration = (1.1 + Math.random() * 0.5) + 's';
    skyZone.appendChild(img);
    img.addEventListener('animationend', function () {
      img.remove();
    });
  }

  function initTimer(opts) {
    var remaining = opts.remainingSeconds;
    var totalSeconds = opts.durationMinutes * 60;
    var lastDroppedMinute = Math.floor(opts.elapsedSeconds / 60);

    var readout = document.getElementById('pomo-readout');
    var skyZone = document.getElementById('pomo-sky');
    var trayCount = document.getElementById('pomo-tray-count');
    var finishForm = document.getElementById('pomo-finish-form');
    var count = lastDroppedMinute;
    if (trayCount) trayCount.textContent = count;
    if (readout) readout.textContent = formatTime(remaining);

    var interval = setInterval(function () {
      remaining -= 1;
      if (readout) readout.textContent = formatTime(Math.max(0, remaining));

      var elapsedNow = totalSeconds - remaining;
      var currentMinute = Math.floor(elapsedNow / 60);
      if (opts.phase === 'focus' && currentMinute > lastDroppedMinute && currentMinute <= opts.durationMinutes) {
        lastDroppedMinute = currentMinute;
        count += 1;
        if (trayCount) trayCount.textContent = count;
        dropCat(skyZone);
      }

      if (remaining <= 0) {
        clearInterval(interval);
        if (finishForm) finishForm.submit();
      }
    }, 1000);
  }

  return { initTimer: initTimer };
})();
