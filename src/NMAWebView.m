#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#include <signal.h>
#include <unistd.h>

@interface NMADelegate : NSObject <NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler>
@property(nonatomic,strong) NSWindow *window;
@property(nonatomic,strong) WKWebView *webView;
@property(nonatomic,strong) NSString *urlString;
@property(nonatomic,strong) NSString *iconPath;
@property(nonatomic,assign) pid_t backendPID;
@end

@implementation NMADelegate
- (void)installStandardMenus {
    NSMenu *main=[[NSMenu alloc] initWithTitle:@""];
    [NSApp setMainMenu:main];

    NSMenuItem *appItem=[[NSMenuItem alloc] initWithTitle:@"NeuroMorph Assessment" action:nil keyEquivalent:@""];
    [main addItem:appItem];
    NSMenu *appMenu=[[NSMenu alloc] initWithTitle:@"NeuroMorph Assessment"];
    [appItem setSubmenu:appMenu];
    NSMenuItem *about=[[NSMenuItem alloc] initWithTitle:@"关于 NeuroMorph Assessment" action:@selector(orderFrontStandardAboutPanel:) keyEquivalent:@""];
    about.target=NSApp; [appMenu addItem:about];
    [appMenu addItem:[NSMenuItem separatorItem]];
    NSMenuItem *quit=[[NSMenuItem alloc] initWithTitle:@"退出 NeuroMorph Assessment" action:@selector(terminate:) keyEquivalent:@"q"];
    quit.target=NSApp; [appMenu addItem:quit];

    NSMenuItem *editItem=[[NSMenuItem alloc] initWithTitle:@"编辑" action:nil keyEquivalent:@""];
    [main addItem:editItem];
    NSMenu *edit=[[NSMenu alloc] initWithTitle:@"编辑"];
    [editItem setSubmenu:edit];
    NSMenuItem *undo=[[NSMenuItem alloc] initWithTitle:@"撤销" action:@selector(undo:) keyEquivalent:@"z"]; undo.target=nil; [edit addItem:undo];
    NSMenuItem *redo=[[NSMenuItem alloc] initWithTitle:@"重做" action:@selector(redo:) keyEquivalent:@"Z"]; redo.target=nil; redo.keyEquivalentModifierMask=NSEventModifierFlagCommand|NSEventModifierFlagShift; [edit addItem:redo];
    [edit addItem:[NSMenuItem separatorItem]];
    NSMenuItem *cut=[[NSMenuItem alloc] initWithTitle:@"剪切" action:@selector(cut:) keyEquivalent:@"x"]; cut.target=nil; [edit addItem:cut];
    NSMenuItem *copy=[[NSMenuItem alloc] initWithTitle:@"复制" action:@selector(copy:) keyEquivalent:@"c"]; copy.target=nil; [edit addItem:copy];
    NSMenuItem *paste=[[NSMenuItem alloc] initWithTitle:@"粘贴" action:@selector(paste:) keyEquivalent:@"v"]; paste.target=nil; [edit addItem:paste];
    NSMenuItem *sel=[[NSMenuItem alloc] initWithTitle:@"全选" action:@selector(selectAll:) keyEquivalent:@"a"]; sel.target=nil; [edit addItem:sel];
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [self installStandardMenus];
    if (self.iconPath.length) {
        NSImage *icon=[[NSImage alloc] initWithContentsOfFile:self.iconPath];
        if (icon) [NSApp setApplicationIconImage:icon];
    }
    NSRect visible=[[NSScreen mainScreen] visibleFrame];
    CGFloat w=MIN(1540.0, MAX(1120.0, visible.size.width*0.96));
    CGFloat h=MIN(960.0, MAX(720.0, visible.size.height*0.94));
    NSRect frame=NSMakeRect(NSMidX(visible)-w/2.0, NSMidY(visible)-h/2.0, w, h);
    self.window=[[NSWindow alloc] initWithContentRect:frame
        styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable)
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title=@"NeuroMorph Assessment (NMA)";
    self.window.delegate=self;
    self.window.minSize=NSMakeSize(1000,650);

    WKWebViewConfiguration *config=[WKWebViewConfiguration new];
    config.websiteDataStore=[WKWebsiteDataStore nonPersistentDataStore];
    [config.userContentController addScriptMessageHandler:self name:@"nmaNative"];
    self.webView=[[WKWebView alloc] initWithFrame:self.window.contentView.bounds configuration:config];
    self.webView.navigationDelegate=self;
    self.webView.UIDelegate=self;
    self.webView.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;
    self.window.contentView=self.webView;
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];

    NSURL *url=[NSURL URLWithString:self.urlString];
    if (!url) {
        NSAlert *a=[NSAlert new]; a.messageText=@"NMA 启动失败"; a.informativeText=@"本地分析服务地址无效。"; [a runModal];
        [NSApp terminate:nil]; return;
    }
    NSMutableURLRequest *req=[NSMutableURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalAndRemoteCacheData timeoutInterval:30.0];
    [self.webView loadRequest:req];
}

// Bridge HTML <input type=file> to Finder (Excel import).
- (void)webView:(WKWebView *)webView
runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters
initiatedByFrame:(WKFrameInfo *)frame
completionHandler:(void (^)(NSArray<NSURL *> *URLs))completionHandler API_AVAILABLE(macos(10.12)) {
    NSOpenPanel *panel=[NSOpenPanel openPanel];
    panel.canChooseFiles=YES;
    panel.canChooseDirectories=parameters.allowsDirectories;
    panel.allowsMultipleSelection=parameters.allowsMultipleSelection;
    panel.resolvesAliases=YES;
    panel.prompt=@"导入";
    panel.message=@"请选择患者 Excel 文件（.xlsx）";
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
    panel.allowedFileTypes=@[@"xlsx"];
#pragma clang diagnostic pop
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result==NSModalResponseOK) completionHandler(panel.URLs ?: @[]);
        else completionHandler(@[]);
    }];
}

- (void)sendPickerResultTarget:(NSString *)target path:(NSString *)path {
    NSDictionary *payload=@{@"target":target ?: @"", @"path":path ?: @""};
    NSData *data=[NSJSONSerialization dataWithJSONObject:payload options:0 error:nil];
    NSString *json=[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    NSString *js=[NSString stringWithFormat:@"window.NMA_NATIVE_PICK_RESULT && window.NMA_NATIVE_PICK_RESULT(%@);", json ?: @"{}"];
    [self.webView evaluateJavaScript:js completionHandler:nil];
}

// Native bridge used by Advanced Settings. Selecting the folders through NSOpenPanel
// is intentional: on macOS it also gives the NMA process user-approved access to
// protected Desktop/Documents locations instead of silently treating them as empty.
- (void)userContentController:(WKUserContentController *)userContentController didReceiveScriptMessage:(WKScriptMessage *)message {
    if (![message.name isEqualToString:@"nmaNative"] || ![message.body isKindOfClass:[NSDictionary class]]) return;
    NSDictionary *body=(NSDictionary *)message.body;
    NSString *action=[body[@"action"] isKindOfClass:[NSString class]] ? body[@"action"] : @"";
    NSString *target=[body[@"target"] isKindOfClass:[NSString class]] ? body[@"target"] : @"";
    NSString *initial=[body[@"initial"] isKindOfClass:[NSString class]] ? body[@"initial"] : @"";
    NSString *prompt=[body[@"prompt"] isKindOfClass:[NSString class]] ? body[@"prompt"] : @"选择";

    if ([action isEqualToString:@"chooseDirectory"]) {
        NSOpenPanel *panel=[NSOpenPanel openPanel];
        panel.canChooseFiles=NO; panel.canChooseDirectories=YES; panel.allowsMultipleSelection=NO;
        panel.canCreateDirectories=YES; panel.resolvesAliases=YES; panel.prompt=@"选择/授权"; panel.message=prompt;
        if (initial.length) {
            NSURL *u=[NSURL fileURLWithPath:[initial stringByExpandingTildeInPath] isDirectory:YES];
            if (u) panel.directoryURL=u;
        }
        [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
            NSString *p=(result==NSModalResponseOK && panel.URL) ? panel.URL.path : @"";
            [self sendPickerResultTarget:target path:p];
        }];
        return;
    }
    if ([action isEqualToString:@"chooseFile"]) {
        NSOpenPanel *panel=[NSOpenPanel openPanel];
        panel.canChooseFiles=YES; panel.canChooseDirectories=NO; panel.allowsMultipleSelection=NO;
        panel.resolvesAliases=YES; panel.prompt=@"选择"; panel.message=prompt;
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
        NSArray *types=[body[@"types"] isKindOfClass:[NSArray class]] ? body[@"types"] : nil;
        if (types.count) panel.allowedFileTypes=types;
#pragma clang diagnostic pop
        if (initial.length) {
            NSURL *u=[NSURL fileURLWithPath:[[initial stringByExpandingTildeInPath] stringByDeletingLastPathComponent] isDirectory:YES];
            if (u) panel.directoryURL=u;
        }
        [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
            NSString *p=(result==NSModalResponseOK && panel.URL) ? panel.URL.path : @"";
            [self sendPickerResultTarget:target path:p];
        }];
    }
}

- (void)webView:(WKWebView *)webView didFailNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    NSAlert *a=[NSAlert new]; a.messageText=@"NMA 页面加载失败"; a.informativeText=error.localizedDescription ?: @"未知错误"; [a runModal];
}
- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    NSAlert *a=[NSAlert new]; a.messageText=@"NMA 页面加载失败"; a.informativeText=error.localizedDescription ?: @"未知错误"; [a runModal];
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return YES; }
- (void)stopBackend {
    if (self.backendPID > 1) {
        if (kill(self.backendPID, 0) == 0) {
            kill(self.backendPID, SIGTERM);
            for (int i=0;i<20;i++) { if (kill(self.backendPID,0)!=0) break; usleep(50000); }
            if (kill(self.backendPID,0)==0) kill(self.backendPID,SIGKILL);
        }
        self.backendPID=0;
    }
}
- (void)applicationWillTerminate:(NSNotification *)notification {
    [self.webView.configuration.userContentController removeScriptMessageHandlerForName:@"nmaNative"];
    [self stopBackend];
}
- (void)windowWillClose:(NSNotification *)notification { [self stopBackend]; }
@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        if (argc < 4) return 2;
        NSApplication *app=[NSApplication sharedApplication];
        NMADelegate *delegate=[NMADelegate new];
        delegate.urlString=[NSString stringWithUTF8String:argv[1]];
        delegate.iconPath=[NSString stringWithUTF8String:argv[2]];
        delegate.backendPID=(pid_t)atoi(argv[3]);
        app.delegate=delegate;
        [app run];
    }
    return 0;
}
