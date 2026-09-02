/* Email body editor: Trix (vendored, static/vendor/trix), configured down to
 * what outgoing email can actually render everywhere — bold, italic, lists,
 * links, and MB's own "button" (a link the send pipeline styles as a colored
 * button; stored as <mb-button> around the anchor text). Headings, quotes,
 * code blocks, and file attachments from Trix's default toolbar are
 * deliberately absent: bodies carry no images or files (attachments ride the
 * email itself, not the body). No inline scripts; CSP is script-src 'self'. */
(function () {
    'use strict';
    if (typeof Trix === 'undefined') return;

    Trix.config.toolbar.getDefaultHTML = function () {
        return '<div class="trix-button-row">' +
            '<span class="trix-button-group trix-button-group--text-tools" data-trix-button-group="text-tools">' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-bold" data-trix-attribute="bold" data-trix-key="b" title="Bold" tabindex="-1">Bold</button>' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-italic" data-trix-attribute="italic" data-trix-key="i" title="Italic" tabindex="-1">Italic</button>' +
            '<button type="button" class="trix-button trix-button--icon trix-button--icon-link" data-trix-attribute="href" data-trix-action="link" data-trix-key="k" title="Link" tabindex="-1">Link</button>' +
            '<button type="button" class="trix-button" data-trix-action="x-mb-button" title="Insert a button (a link styled as a button in the email)" tabindex="-1">Button</button>' +
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
            '</div></div></div></div>';
    };

    // The button marker. Survives the editor round-trip as <mb-button> around
    // the link text; core/email_html.py turns it into an email-safe styled
    // anchor at send time. Not inheritable: typing after a button is plain.
    Trix.config.textAttributes.mbButton = { tagName: 'mb-button', inheritable: false };
    // Trix sanitizes loaded HTML with its bundled DOMPurify, which strips
    // unknown elements — without this, a saved button vanishes on re-edit.
    Trix.config.dompurify.ADD_TAGS = ['mb-button'];

    // No files or images in bodies (drag-drop and paste included).
    addEventListener('trix-file-accept', function (e) { e.preventDefault(); });

    // Insert-button flow: use the selected text as the label, else ask for
    // one; ask for the address; apply link + button marker together.
    addEventListener('trix-action-invoke', function (e) {
        if (e.actionName !== 'x-mb-button') return;
        var editor = e.target.editor;
        if (!editor) return;
        var range = editor.getSelectedRange();
        var text = editor.getDocument().getStringAtRange(range);
        if (!text) {
            text = window.prompt('Button text:', '');
            if (!text) return;
        }
        var url = window.prompt('Button link (https://…):', 'https://');
        if (!url || url === 'https://') return;
        editor.recordUndoEntry('Insert button');
        if (range[0] === range[1]) {
            editor.insertString(text);
            editor.setSelectedRange([range[0], range[0] + text.length]);
        }
        editor.activateAttribute('href', url);
        editor.activateAttribute('mbButton');
        // Collapse past the button so continued typing is plain text.
        var end = editor.getSelectedRange()[1];
        editor.setSelectedRange([end, end]);
        editor.deactivateAttribute('mbButton');
        editor.deactivateAttribute('href');
    });
})();
