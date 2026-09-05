/*
 * Builds Apprise Mobile URLs and branded QR codes with the vendored `qrcode`
 * library. Callers supply translated labels because this file is not processed
 * as a Django template.
 */
(function (global) {
  "use strict";

  function encodePart(value) {
    return encodeURIComponent(value || "");
  }

  // A leading colon preserves a known username when its password is unavailable.
  function buildUserInfo(username, password) {
    if (username && password) {
      return encodePart(username) + ":" + encodePart(password) + "@";
    }
    if (password) {
      return encodePart(password) + "@";
    }
    if (username) {
      return ":" + encodePart(username) + "@";
    }
    return "";
  }

  function buildAppriseMobileUrl(parts) {
    const scheme = parts.secure ? "apprises" : "apprise";
    const userinfo = buildUserInfo(parts.username, parts.password);
    const host = parts.host || "";
    const base = parts.base || "";
    const configId = parts.configId || "";
    return scheme + "://" + userinfo + host + base + "/" + configId;
  }

  // Trust the server's flag instead of inferring which credentials were used.
  function usesAdminCredentials(data) {
    return Boolean(data && data.uses_admin_credentials === true);
  }

  // Read the shared warning text rendered once in base.html.
  function adminCredentialsWarning() {
    const source = global.document && global.document.getElementById("apprise-mobile-admin-warning-copy");
    return {
      title: (source && source.dataset.title) || "",
      text: (source && source.dataset.text) || ""
    };
  }

  // Shorten the Config ID while keeping enough context to identify it.
  function redactConfigId(url) {
    const value = String(url || "");
    const suffixAt = value.search(/[?#]/);
    const core = suffixAt >= 0 ? value.slice(0, suffixAt) : value;
    const suffix = suffixAt >= 0 ? value.slice(suffixAt) : "";
    const slashAt = core.lastIndexOf("/");
    const configId = slashAt >= 0 ? core.slice(slashAt + 1) : "";
    if (!configId) {
      return value;
    }
    const shortened = configId.length > 2
      ? configId.charAt(0) + "..." + configId.charAt(configId.length - 1)
      : "*".repeat(configId.length);
    return core.slice(0, slashAt + 1) + shortened + suffix;
  }

  // Keep secrets in the QR payload while concealing the displayed URL.
  function redactMobileUrl(url) {
    const match = String(url || "").match(/^((?:apprise|apprises):\/\/)([^/@]*)@(.*)$/i);
    if (!match) {
      return redactConfigId(url);
    }
    const userinfo = match[2];
    const separator = userinfo.indexOf(":");
    let redacted;
    if (userinfo.charAt(0) === ":") {
      redacted = userinfo.slice(1) + ":*****";
    } else if (separator >= 0) {
      redacted = userinfo.slice(0, separator) + ":*****";
    } else {
      redacted = "*****";
    }
    return redactConfigId(match[1] + redacted + "@" + match[3]);
  }

  // High error correction leaves room for the center logo. Oversized payloads
  // become rejected promises so every caller can handle them the same way.
  function drawQrToCanvas(canvas, text, options) {
    try {
      const opts = options || {};
      const cellPx = opts.cellPx || 8;
      // Keep the QR spec's four-module blank border for reliable camera scans.
      const margin = opts.margin != null ? opts.margin : cellPx * 4;
      const qr = global.qrcode(0, "H");
      qr.addData(text);
      qr.make();

      const count = qr.getModuleCount();
      const size = count * cellPx + margin * 2;
      canvas.width = size;
      canvas.height = size;

      const ctx = canvas.getContext("2d");
      ctx.fillStyle = opts.background || "#ffffff";
      ctx.fillRect(0, 0, size, size);
      ctx.fillStyle = opts.foreground || "#000000";
      for (let row = 0; row < count; row += 1) {
        for (let col = 0; col < count; col += 1) {
          if (qr.isDark(row, col)) {
            ctx.fillRect(margin + col * cellPx, margin + row * cellPx, cellPx, cellPx);
          }
        }
      }

      if (!opts.logoSrc) {
        return Promise.resolve(canvas);
      }

      return new Promise(function (resolve) {
        const img = new Image();
        img.onload = function () {
          // Sized relative to the finished canvas so the badge scales with it.
          const logoSize = Math.round(size * (opts.logoScale || 0.22));
          const badgeRadius = Math.round(logoSize * 0.62);
          const cx = size / 2;
          const cy = size / 2;

          ctx.save();
          ctx.fillStyle = "#ffffff";
          ctx.beginPath();
          ctx.arc(cx, cy, badgeRadius, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();

          ctx.drawImage(img, cx - logoSize / 2, cy - logoSize / 2, logoSize, logoSize);
          resolve(canvas);
        };
        img.onerror = function () {
          // A missing logo should never block the QR code itself from showing.
          resolve(canvas);
        };
        img.src = opts.logoSrc;
      });
    } catch (error) {
      return Promise.reject(error);
    }
  }

  // Draw the shared popup and connect its copy button after it opens.
  function showPopup(options) {
    const canvasId = "apprise-qr-canvas-" + Math.random().toString(36).slice(2);
    const html =
      '<div class="apprise-qr-popup">' +
        '<div class="apprise-qr-art">' +
          '<canvas id="' + canvasId + '" class="apprise-qr-canvas"></canvas>' +
          (options.logoSrc
            ? '<span class="apprise-qr-logo"><img alt=""></span>'
            : "") +
        "</div>" +
        '<div class="apprise-qr-url-row">' +
          '<code class="apprise-qr-url"></code>' +
          '<span class="apprise-qr-url-actions">' +
            '<button type="button" class="btn-flat btn-small apprise-qr-visibility" ' +
              'aria-pressed="false" aria-label="' + (options.showUrlLabel || "") +
              '" title="' + (options.showUrlLabel || "") + '">' +
              '<i class="material-icons" aria-hidden="true">visibility_off</i>' +
            "</button>" +
            '<button type="button" class="btn-flat btn-small apprise-qr-copy" ' +
              'aria-label="' + (options.copyLabel || "") + '" title="' + (options.copyLabel || "") + '">' +
              '<i class="material-icons" aria-hidden="true">content_copy</i>' +
            "</button>" +
          "</span>" +
        "</div>" +
        (options.note
          ? '<div class="apprise-qr-note' + (options.warning ? " apprise-qr-note--warning" : "") +
            '">' +
              (options.warning && options.warningIconHtml
                ? '<span class="apprise-qr-warning-icon">' + options.warningIconHtml + "</span>"
                : "") +
              '<span class="apprise-qr-note-copy">' +
                (options.warning && options.warningTitle
                  ? '<strong class="apprise-qr-warning-title">' + options.warningTitle + "</strong>"
                  : "") +
                '<span class="apprise-qr-note-text">' + options.note + "</span>" +
              "</span>" +
            "</div>"
          : "") +
      "</div>";

    return global.appriseFire(
      Object.assign(
        {
          title: options.title,
          html: html,
          customClass: {popup: "apprise-popup--qr"},
          focusConfirm: false,
          didOpen: function () {
            const container = global.Swal.getHtmlContainer();
            if (!container) {
              return;
            }
            const canvas = container.querySelector("#" + canvasId);
            const logoEl = container.querySelector(".apprise-qr-logo img");
            const urlEl = container.querySelector(".apprise-qr-url");
            const visibilityBtn = container.querySelector(".apprise-qr-visibility");
            const copyBtn = container.querySelector(".apprise-qr-copy");
            if (logoEl) {
              logoEl.addEventListener("error", function () {
                // A missing logo must not leave a blank patch over the QR code.
                logoEl.parentNode.remove();
              });
              logoEl.src = options.logoSrc;
            }
            if (urlEl) {
              urlEl.textContent = redactMobileUrl(options.url);
            }
            if (visibilityBtn && urlEl) {
              visibilityBtn.addEventListener("click", function () {
                const reveal = visibilityBtn.getAttribute("aria-pressed") !== "true";
                urlEl.textContent = reveal ? options.url : redactMobileUrl(options.url);
                visibilityBtn.setAttribute("aria-pressed", reveal ? "true" : "false");
                visibilityBtn.setAttribute(
                  "aria-label",
                  reveal ? (options.hideUrlLabel || "") : (options.showUrlLabel || "")
                );
                visibilityBtn.title = visibilityBtn.getAttribute("aria-label");
                visibilityBtn.querySelector("i").textContent = reveal
                  ? "visibility"
                  : "visibility_off";
              });
            }
            if (canvas) {
              // Keep the smooth logo separate from the crisp-edged QR canvas.
              drawQrToCanvas(canvas, options.url).catch(function () {
                // Keep an oversized URL copyable when no QR can be drawn.
                if (canvas.parentNode) {
                  const errorEl = document.createElement("p");
                  errorEl.className = "apprise-qr-error";
                  errorEl.textContent = options.qrErrorMessage || "";
                  canvas.parentNode.replaceChild(errorEl, canvas);
                }
              });
            }
            if (copyBtn) {
              copyBtn.addEventListener("click", function () {
                global.appriseCopyToClipboard(options.url, options.copyMessage);
              });
            }
          }
        },
        options.fireOverrides || {}
      )
    );
  }

  global.AppriseQr = {
    buildAppriseMobileUrl: buildAppriseMobileUrl,
    redactMobileUrl: redactMobileUrl,
    drawQrToCanvas: drawQrToCanvas,
    showPopup: showPopup,
    usesAdminCredentials: usesAdminCredentials,
    adminCredentialsWarning: adminCredentialsWarning
  };
})(window);
