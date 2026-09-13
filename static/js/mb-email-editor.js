/* Email body editor: Trix (vendored, static/vendor/trix), configured down to
 * what outgoing email can actually render everywhere — bold, italic, lists,
 * links, and MB's own "button" (a link the send pipeline styles as a colored
 * button; stored as <mb-button>, <mb-button-center>, or <mb-button-right>
 * around the anchor text — the tag carries the button's position). Headings,
 * quotes, code blocks, and file attachments from Trix's default toolbar are
 * deliberately absent: bodies carry no images or files (attachments ride the
 * email itself, not the body). No inline scripts; CSP is script-src 'self'. */
(function () {
    'use strict';
    if (typeof Trix === 'undefined') return;

    // Position → text attribute; attribute → position. <mb-button> (left) is
    // the original marker, so bodies saved before positions existed still
    // edit and send unchanged.
    var ALIGN_ATTR = { left: 'mbButton', center: 'mbButtonCenter', right: 'mbButtonRight' };
    var ATTR_ALIGN = { mbButton: 'left', mbButtonCenter: 'center', mbButtonRight: 'right' };
    var LAST_ALIGN_KEY = 'mb-email-button-align';

    Trix.config.toolbar.getDefaultHTML = function () {
        return '<div class="trix-button-row">' +
            '<span class="trix-button-group trix-button-group--text-tools" data-trix-button-group="text-tools">' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-bold" data-trix-attribute="bold" data-trix-key="b" title="Bold" tabindex="-1">Bold</button>' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-italic" data-trix-attribute="italic" data-trix-key="i" title="Italic" tabindex="-1">Italic</button>' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-link" data-trix-attribute="href" data-trix-action="link" data-trix-key="k" title="Link" tabindex="-1">Link</button>' +
            '<button type="button" class="trix-button" data-trix-action="x-mb-button" title="Insert a button (a link styled as a button in the email). Click inside an existing button to edit it." tabindex="-1">Button</button>' +
            '</span>' +
            '<span class="trix-button-group trix-button-group--block-tools" data-trix-button-group="block-tools">' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-bullet-list" data-trix-attribute="bullet" title="Bullet list" tabindex="-1">Bullets</button>' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-number-list" data-trix-attribute="number" title="Numbered list" tabindex="-1">Numbers</button>' +
            '</span>' +
            '<span class="trix-button-group-spacer"></span>' +
            '<span class="trix-button-group trix-button-group--history-tools" data-trix-button-group="history-tools">' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-undo" data-trix-action="undo" data-trix-key="z" title="Undo" tabindex="-1">Undo</button>' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-redo" data-trix-action="redo" data-trix-key="shift+z" title="Redo" tabindex="-1">Redo</button>' +
            '</span>' +
            '</div>' +
            '<div class="trix-dialogs" data-trix-dialogs>' +
            '<div class="trix-dialog trix-dialog--link" data-trix-dialog="href" data-trix-dialog-attribute="href">' +
            '<div class="trix-dialog__link-fields">' +
            '<input type="url" name="href" class="trix-input trix-input--dialog" placeholder="https://…" aria-label="URL" data-trix-validate-href required data-trix-input>' +
            '<div class="trix-button-group">' +
            '<input type="button" class="trix-button trix-button--dialog" value="Link" data-trix-method="setAttribute">' +
            '<input type="button" class="trix-button trix-button--dialog" value="Unlink" data-trix-method="removeAttribute">' +
            '</div></div></div>' +
            // The button dialog. Not a Trix attribute dialog (Trix's own can
            // only set one attribute); shown and applied by the code below.
            '<div class="trix-dialog mb-button-dialog" data-mb-button-dialog role="dialog" aria-label="Button">' +
            '<div class="mb-button-dialog__fields">' +
            '<label class="mb-button-dialog__field"><span>Button text</span>' +
            '<input type="text" class="trix-input trix-input--dialog" data-mb-button-text placeholder="Leave a review" autocomplete="off"></label>' +
            '<label class="mb-button-dialog__field"><span>Link</span>' +
            '<input type="url" class="trix-input trix-input--dialog" data-mb-button-url placeholder="https://…" autocomplete="off"></label>' +
            '<label class="mb-button-dialog__field mb-button-dialog__field--narrow"><span>Position</span>' +
            '<select class="trix-input trix-input--dialog" data-mb-button-align>' +
            '<option value="left">Left</option><option value="center">Center</option><option value="right">Right</option>' +
            '</select></label>' +
            '</div>' +
            '<div class="mb-button-dialog__actions">' +
            '<div class="trix-button-group">' +
            '<input type="button" class="trix-button trix-button--dialog" value="Insert" data-mb-button-apply>' +
            '<input type="button" class="trix-button trix-button--dialog" value="Remove" data-mb-button-remove hidden>' +
            '<input type="button" class="trix-button trix-button--dialog" value="Cancel" data-mb-button-cancel>' +
            '</div>' +
            '<span class="mb-button-dialog__error" data-mb-button-error hidden></span>' +
            '</div></div>' +
            '</div>';
    };

    // The button markers. Survive the editor round-trip as a tag around the
    // link text; core/email_html.py turns each into an email-safe styled
    // anchor at send time. Not inheritable: typing after a button is plain.
    Trix.config.textAttributes.mbButton = { tagName: 'mb-button', inheritable: false };
    Trix.config.textAttributes.mbButtonCenter = { tagName: 'mb-button-center', inheritable: false };
    Trix.config.textAttributes.mbButtonRight = { tagName: 'mb-button-right', inheritable: false };
    // Trix sanitizes loaded HTML with its bundled DOMPurify, which strips
    // unknown elements — without this, a saved button vanishes on re-edit.
    Trix.config.dompurify.ADD_TAGS = ['mb-button', 'mb-button-center', 'mb-button-right'];

    // No files or images in bodies (drag-drop and paste included).
    addEventListener('trix-file-accept', function (e) { e.preventDefault(); });

    // New buttons start at the position used last (centered until then).
    function rememberedAlign() {
        try {
            var v = localStorage.getItem(LAST_ALIGN_KEY);
            if (ALIGN_ATTR[v]) return v;
        } catch (e) { /* storage blocked: fall through */ }
        return 'center';
    }
    function rememberAlign(align) {
        try { localStorage.setItem(LAST_ALIGN_KEY, align); } catch (e) { /* ignore */ }
    }

    function buttonAttrIn(attrs) {
        for (var name in ATTR_ALIGN) if (attrs && attrs[name]) return name;
        return null;
    }

    // Whether the character at position p carries the button attribute.
    function hasAttrAt(doc, p, attrName) {
        if (p < 0 || p >= doc.getLength()) return false;
        return !!doc.getCommonAttributesAtRange([p, p + 1])[attrName];
    }

    // Whether the character at position p belongs to the same button as the
    // seed: same position attribute AND the same link. Two adjacent buttons
    // with the same position but different links are two buttons.
    function sameButtonAt(doc, p, attrName, href) {
        if (p < 0 || p >= doc.getLength()) return false;
        var attrs = doc.getCommonAttributesAtRange([p, p + 1]);
        return !!attrs[attrName] && (attrs.href || '') === href;
    }

    // p if the character at p carries any button marker, else -1.
    function buttonSeedAt(doc, p) {
        for (var name in ATTR_ALIGN) if (hasAttrAt(doc, p, name)) return p;
        return -1;
    }

    // The whole run of one button around a position known to be inside it
    // (Trix reports attributes; the run's edges are found by walking).
    function buttonRunAt(doc, seed, attrName) {
        var href = doc.getCommonAttributesAtRange([seed, seed + 1]).href || '';
        var start = seed, end = seed + 1;
        while (sameButtonAt(doc, start - 1, attrName, href)) start--;
        while (sameButtonAt(doc, end, attrName, href)) end++;
        return [start, end];
    }

    function q(dialog, sel) { return dialog.querySelector(sel); }

    function showError(dialog, msg) {
        var el = q(dialog, '[data-mb-button-error]');
        el.textContent = msg;
        el.hidden = !msg;
    }

    function openDialog(dialog, state) {
        dialog._mb = state;
        q(dialog, '[data-mb-button-text]').value = state.text || '';
        q(dialog, '[data-mb-button-url]').value = state.href || '';
        q(dialog, '[data-mb-button-align]').value = state.align;
        q(dialog, '[data-mb-button-apply]').value = state.existing ? 'Update' : 'Insert';
        q(dialog, '[data-mb-button-remove]').hidden = !state.existing;
        showError(dialog, '');
        dialog.setAttribute('data-trix-active', '');
        dialog.classList.add('trix-active');
        q(dialog, state.text ? '[data-mb-button-url]' : '[data-mb-button-text]').focus();
    }

    function closeDialog(dialog, restoreSelection) {
        var state = dialog._mb;
        dialog._mb = null;
        dialog.removeAttribute('data-trix-active');
        dialog.classList.remove('trix-active');
        if (restoreSelection && state) state.editor.setSelectedRange(state.range);
    }

    // A usable link, or null. A bare host gets https://; the address is
    // inserted as typed (no normalizing), so a shop's review link stays exact.
    function usableUrl(raw) {
        var s = (raw || '').trim();
        if (!s || /\s/.test(s)) return null;   // no address has whitespace in it
        if (!/^[a-z][a-z0-9+.-]*:/i.test(s)) s = 'https://' + s;
        try {
            var u = new URL(s);
            if (['http:', 'https:', 'mailto:'].indexOf(u.protocol) === -1) return null;
        } catch (e) { return null; }
        return s;
    }

    function applyButton(dialog) {
        var state = dialog._mb;
        if (!state) return;
        var text = q(dialog, '[data-mb-button-text]').value.trim();
        var url = usableUrl(q(dialog, '[data-mb-button-url]').value);
        var align = q(dialog, '[data-mb-button-align]').value;
        if (!ALIGN_ATTR[align]) align = 'center';
        if (!text) {
            showError(dialog, 'Give the button some text.');
            q(dialog, '[data-mb-button-text]').focus();
            return;
        }
        if (!url) {
            showError(dialog, 'Enter a web address (https://…) or a mailto: link.');
            q(dialog, '[data-mb-button-url]').focus();
            return;
        }
        var editor = state.editor, range = state.range, start = range[0];
        editor.setSelectedRange(range);
        editor.recordUndoEntry(state.existing ? 'Edit button' : 'Insert button');
        // Clear every marker and link on the range, not only the one the
        // dialog was opened for: a selection that overhangs a button would
        // otherwise keep the old marker under the new one.
        clearButton(editor);
        if (range[0] === range[1] || text !== state.text) {
            editor.insertString(text);   // replaces the selection, or inserts at the caret
        }
        editor.setSelectedRange([start, start + text.length]);
        editor.activateAttribute('href', url);
        editor.activateAttribute(ALIGN_ATTR[align]);
        // Collapse past the button so continued typing is plain text.
        editor.setSelectedRange([start + text.length, start + text.length]);
        editor.deactivateAttribute(ALIGN_ATTR[align]);
        editor.deactivateAttribute('href');
        rememberAlign(align);
        closeDialog(dialog, false);
    }

    // Drop every marker attribute and the link from the current selection.
    function clearButton(editor) {
        for (var name in ATTR_ALIGN) editor.deactivateAttribute(name);
        editor.deactivateAttribute('href');
    }

    function removeButton(dialog) {
        var state = dialog._mb;
        if (!state || !state.existing) return;
        var editor = state.editor;
        editor.setSelectedRange(state.range);
        editor.recordUndoEntry('Remove button');
        clearButton(editor);
        editor.setSelectedRange([state.range[1], state.range[1]]);
        closeDialog(dialog, false);
    }

    // Toolbar Button: open the dialog for a new button on the selection, or
    // for the existing button under the caret.
    addEventListener('trix-action-invoke', function (e) {
        if (e.actionName !== 'x-mb-button') return;
        var editorElement = e.target, editor = editorElement.editor;
        var toolbar = editorElement.toolbarElement;
        var dialog = toolbar && toolbar.querySelector('[data-mb-button-dialog]');
        if (!editor || !dialog) return;
        if (dialog._mb) { closeDialog(dialog, true); return; }
        var doc = editor.getDocument();
        var range = editor.getSelectedRange();
        var existing = null, seed = -1;
        if (range[0] === range[1]) {
            // A caret: the button it just left (to the left) wins, so that
            // "Insert, spot a typo, press Button again" edits the new button;
            // otherwise the button it is about to enter.
            seed = buttonSeedAt(doc, range[0] - 1);
            if (seed < 0) seed = buttonSeedAt(doc, range[0]);
            if (seed >= 0) existing = buttonAttrIn(doc.getCommonAttributesAtRange([seed, seed + 1]));
        } else {
            existing = buttonAttrIn(doc.getCommonAttributesAtRange(range));
            if (existing) {
                seed = hasAttrAt(doc, range[0], existing) ? range[0] : -1;
                if (seed < 0) existing = null;
            }
        }
        var editRange = range, href = '', align = rememberedAlign();
        if (existing) {
            editRange = buttonRunAt(doc, seed, existing);
            href = doc.getCommonAttributesAtRange(editRange).href || '';
            align = ATTR_ALIGN[existing];
        }
        openDialog(dialog, {
            editor: editor, range: editRange, existing: existing,
            text: doc.getStringAtRange(editRange), href: href, align: align
        });
    });

    document.addEventListener('click', function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        var dialog = t.closest('[data-mb-button-dialog]');
        if (!dialog) return;
        if (t.matches('[data-mb-button-apply]')) applyButton(dialog);
        else if (t.matches('[data-mb-button-remove]')) removeButton(dialog);
        else if (t.matches('[data-mb-button-cancel]')) closeDialog(dialog, true);
    });

    // Enter applies, Escape cancels. Enter must not submit the page's form
    // (the toolbar lives inside it).
    document.addEventListener('keydown', function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        var dialog = t.closest('[data-mb-button-dialog]');
        if (!dialog) return;
        if (e.key === 'Escape') { e.preventDefault(); closeDialog(dialog, true); }
        else if (e.key === 'Enter' && t.tagName !== 'SELECT' && !t.matches('input[type=button]')) {
            e.preventDefault(); applyButton(dialog);   // a focused Cancel/Remove/Insert gets its own click
        }
    });

    // The dialog holds the range it was opened on. Editing underneath it
    // would shift that range, so the dialog closes the moment the editor gets
    // focus back (Trix's own link dialog does the same).
    addEventListener('trix-focus', function (e) {
        var toolbar = e.target && e.target.toolbarElement;
        var dialog = toolbar && toolbar.querySelector('[data-mb-button-dialog]');
        if (dialog && dialog._mb) closeDialog(dialog, false);
    });
})();
