// HydraDataHive — UI helpers
(function () {
    // Render any server-side flash messages via SweetAlert toasts.
    if (window.__hydraFlashes) {
        window.__hydraFlashes.forEach(function (m) {
            const [cat, text] = m;
            const icon = ({ error: 'error', message: 'info', info: 'info', success: 'success' })[cat] || 'info';
            Swal.fire({ toast: true, position: 'top-end', icon: icon, title: text, timer: 2500, showConfirmButton: false });
        });
    }
})();

function confirmDelete(name) {
    return Swal.fire({
        title: 'Delete ' + name + '?',
        text: 'This removes the file and its chunks locally. Other nodes will be notified on next sync.',
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: 'Delete',
        confirmButtonColor: '#dc3545'
    }).then(function (r) { return r.isConfirmed; });
}

function confirmAction(title, body, btnText) {
    return Swal.fire({
        title: title,
        text: body,
        icon: 'question',
        showCancelButton: true,
        confirmButtonText: btnText
    }).then(function (r) { return r.isConfirmed; });
}

function verifyChain(e) {
    e.preventDefault();
    fetch('/api/v1/audit/verify').then(function (r) { return r.json(); }).then(function (j) {
        if (j.ok) {
            Swal.fire({ icon: 'success', title: 'Chain OK', text: j.checked + ' entries verified.' });
        } else {
            Swal.fire({ icon: 'error', title: 'Chain BROKEN', text: 'Bad ids: ' + (j.bad_ids || []).join(', ') });
        }
    }).catch(function (err) {
        Swal.fire({ icon: 'error', title: 'Verify failed', text: String(err) });
    });
    return false;
}