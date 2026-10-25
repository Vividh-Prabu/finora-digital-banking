(function () {
    "use strict";

    // Mobile navigation
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.getElementById("site-nav");
    if (toggle && nav) {
        toggle.addEventListener("click", function () {
            var open = nav.classList.toggle("open");
            toggle.setAttribute("aria-expanded", String(open));
        });
    }

    // Dismissible flash messages (success messages fade out on their own)
    document.querySelectorAll(".flash").forEach(function (flash) {
        var close = flash.querySelector(".flash-close");
        if (close) close.addEventListener("click", function () { flash.remove(); });
        if (flash.classList.contains("flash-success")) {
            setTimeout(function () { flash.style.transition = "opacity .5s"; flash.style.opacity = "0"; }, 6000);
            setTimeout(function () { flash.remove(); }, 6600);
        }
    });

    // Live table search
    document.querySelectorAll("input[data-filter]").forEach(function (input) {
        var table = document.getElementById(input.dataset.filter);
        if (!table) return;
        var panel = table.closest(".panel");
        var empty = panel && panel.querySelector(".no-match");
        input.addEventListener("input", function () {
            var q = input.value.trim().toLowerCase();
            var visible = 0;
            table.querySelectorAll("tbody tr").forEach(function (row) {
                var match = !q || row.textContent.toLowerCase().indexOf(q) !== -1;
                row.hidden = !match;
                if (match) visible++;
            });
            if (empty) empty.hidden = visible !== 0;
        });
    });

    // Confirm destructive actions
    document.addEventListener("click", function (event) {
        var target = event.target.closest("[data-confirm]");
        if (target && !window.confirm(target.dataset.confirm)) event.preventDefault();
    });

    // Prevent double submits
    document.querySelectorAll("form").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (event.defaultPrevented) return;
            setTimeout(function () {
                form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; });
            }, 0);
        });
    });

    // Re-enable buttons when restoring from the back/forward cache
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) document.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = false; });
    });
})();
