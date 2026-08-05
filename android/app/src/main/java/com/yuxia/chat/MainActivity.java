package com.yuxia.chat;

import android.content.res.AssetManager;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import androidx.appcompat.app.AppCompatActivity;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

public class MainActivity extends AppCompatActivity {

    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // 复制静态文件到内部存储
        copyAssets("static", getFilesDir());

        // 启动 Flask 服务（在 Python 线程中）
        startFlaskServer();

        // 初始化 WebView
        webView = new WebView(this);
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setAllowFileAccess(true);
        webView.setWebViewClient(new WebViewClient());
        webView.loadUrl("http://localhost:5000");
        setContentView(webView);
    }

    private void startFlaskServer() {
        new Thread(() -> {
            try {
                // 调用 Python 入口
                com.chaquo.python.Python python = com.chaquo.python.Python.getInstance();
                python.getModule("android_launcher").callAttr("start_flask", getFilesDir().getAbsolutePath());
            } catch (Exception e) {
                e.printStackTrace();
            }
        }).start();
    }

    private void copyAssets(String path, File destDir) {
        AssetManager am = getAssets();
        try {
            String[] files = am.list(path);
            if (files == null || files.length == 0) {
                // 文件，复制
                File outFile = new File(destDir, path);
                outFile.getParentFile().mkdirs();
                try (InputStream in = am.open(path);
                     OutputStream out = new FileOutputStream(outFile)) {
                    byte[] buf = new byte[8192];
                    int len;
                    while ((len = in.read(buf)) > 0) out.write(buf, 0, len);
                }
                return;
            }
            // 目录，递归
            for (String f : files) {
                copyAssets(path + "/" + f, destDir);
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}