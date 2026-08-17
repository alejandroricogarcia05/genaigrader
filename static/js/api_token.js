(function () {
  "use strict";

  function getCookie(name) {
    var cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      var cookies = document.cookie.split(";");
      for (var i = 0; i < cookies.length; i++) {
        var cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === name + "=") {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  function showToast(message, type) {
    var container = document.getElementById("toast-container");
    if (!container) return;

    var el = document.createElement("div");
    el.className = "toast toast-slide-in toast-" + (type || "info");
    el.setAttribute("role", "alert");
    el.setAttribute("aria-live", "polite");

    var body = document.createElement("div");
    body.className = "toast-body";
    body.textContent = message;

    var closeBtn = document.createElement("button");
    closeBtn.className = "toast-close";
    closeBtn.innerHTML = "&times;";
    closeBtn.setAttribute("aria-label", "Dismiss notification");
    closeBtn.onclick = function () {
      if (el.parentNode) el.parentNode.removeChild(el);
    };

    el.appendChild(body);
    el.appendChild(closeBtn);
    container.appendChild(el);

    setTimeout(function () {
      el.classList.remove("toast-slide-in");
      el.classList.add("toast-slide-out");
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 300);
    }, 8000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var copyBtn = document.getElementById("copy-token-btn");
    var tokenInput = document.getElementById("api-token-input");
    var rotateForm = document.getElementById("rotate-token-form");
    var rotateBtn = document.getElementById("rotate-token-btn");

    if (copyBtn && tokenInput) {
      copyBtn.addEventListener("click", function () {
        var token = tokenInput.value;
        if (navigator.clipboard) {
          navigator.clipboard.writeText(token).then(function () {
            showToast("Token copied to clipboard", "success");
          });
        } else {
          tokenInput.removeAttribute("disabled");
          tokenInput.select();
          document.execCommand("copy");
          tokenInput.setAttribute("disabled", "");
          showToast("Token copied to clipboard", "success");
        }
      });
    }

    if (rotateForm && rotateBtn) {
      rotateBtn.addEventListener("click", function (e) {
        e.preventDefault();
        if (!confirm("Are you sure? Your current token will stop working immediately.")) {
          return;
        }
        var formData = new FormData(rotateForm);
        fetch(window.location.href, {
          method: "POST",
          body: formData,
          headers: { "X-CSRFToken": getCookie("csrftoken") },
        }).then(function () {
          window.location.reload();
        });
      });
    }
  });
})();