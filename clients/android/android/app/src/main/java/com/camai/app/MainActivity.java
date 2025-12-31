package com.camai.app;

import android.os.Bundle;
import android.webkit.WebView;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Reduce WebView logging noise from MJPEG stream redraws
        WebView.setWebContentsDebuggingEnabled(false);
    }
}
