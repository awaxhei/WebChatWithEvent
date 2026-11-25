/**
 * 下载入口旁边显示"当前提供的是哪一版 APK"。
 *
 * 装上之后手机上还有一处对照：App 的「关于与更新日志」里写着版本号与安装时间。
 * 两边一致，就说明拿到的是新包，而不是浏览器或下载目录里留下的旧文件。
 */
(function () {
    var box = document.getElementById('apkVersionInfo');
    if (!box) return;

    function render(text) {
        box.textContent = text;
    }

    fetch('/api/apk_info', { cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
            if (!d || !d.version) {
                render('尚未构建 APK。');
                return;
            }
            var parts = ['当前版本 ' + d.version + '（' + d.version_code + '）'];
            if (d.built_at) parts.push('构建于 ' + d.built_at);
            if (d.size_mb) parts.push(d.size_mb + ' MB');
            render(parts.join(' · '));
        })
        .catch(function () {
            render('');
        });
})();
