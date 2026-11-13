// Small macOS application: notifications retain their own local image paths.
#import <Cocoa/Cocoa.h>
#import <UserNotifications/UserNotifications.h>

static NSURL *existingTarget(NSString *path, NSString *folder, BOOL folderOnly) {
    NSFileManager *files = NSFileManager.defaultManager;
    NSArray *candidates = folderOnly ? @[folder ?: @""] : @[path ?: @"", folder ?: @""];
    for (NSString *candidate in candidates) {
        if (candidate.isAbsolutePath && [files fileExistsAtPath:candidate])
            return [NSURL fileURLWithPath:candidate];
    }
    return nil;
}

@interface RenderNotifier : NSObject <NSApplicationDelegate, UNUserNotificationCenterDelegate>
@end

@implementation RenderNotifier
- (void)applicationWillFinishLaunching:(NSNotification *)notification {
    UNUserNotificationCenter.currentNotificationCenter.delegate = self;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSArray<NSString *> *args = NSProcessInfo.processInfo.arguments;
    NSString *action = args.count > 1 ? args[1] : @"";
    if ([action isEqualToString:@"--register"]) {
        // Opening this mode through LaunchServices registers the current app
        // path for notification clicks without requesting permission or posting.
        [NSApp terminate:nil];
        return;
    }
    BOOL setup = [action isEqualToString:@"--setup"];
    BOOL status = [action isEqualToString:@"--status"];
    BOOL test = [action isEqualToString:@"--test"];
    BOOL render = [action isEqualToString:@"--notify"];
    if (!setup && !status && !test && !render) {
        // A notification click relaunches the app without CLI arguments.
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 15 * NSEC_PER_SEC),
                       dispatch_get_main_queue(), ^{ [NSApp terminate:nil]; });
        return;
    }
    if ((render && args.count != 7) || (!render && args.count != 2)) {
        fprintf(stderr, "Invalid notification helper arguments\n");
        exit(2);
    }
    if (render && !existingTarget(args[5], args[6], NO)) {
        fprintf(stderr, "Render and folder are missing\n");
        exit(2);
    }
    UNUserNotificationCenter *center = UNUserNotificationCenter.currentNotificationCenter;
    UNNotificationAction *finder = [UNNotificationAction actionWithIdentifier:@"show-in-finder"
        title:@"Show in Finder" options:UNNotificationActionOptionForeground];
    UNNotificationAction *image = [UNNotificationAction actionWithIdentifier:@"open-image"
        title:@"Open Image" options:UNNotificationActionOptionForeground];
    UNNotificationCategory *category = [UNNotificationCategory categoryWithIdentifier:@"render-ready"
        actions:@[finder, image] intentIdentifiers:@[] options:UNNotificationCategoryOptionNone];
    [center setNotificationCategories:[NSSet setWithObject:category]];

    void (^post)(void) = ^{
        UNMutableNotificationContent *content = [UNMutableNotificationContent new];
        content.title = render ? args[3] : @"Blender render notifications are ready";
        content.body = render ? args[4] : @"You'll be notified when a render is downloaded and verified.";
        content.sound = UNNotificationSound.defaultSound;
        if (render) {
            content.categoryIdentifier = @"render-ready";
            content.userInfo = @{ @"image": args[5], @"folder": args[6] };
        }
        UNNotificationRequest *request = [UNNotificationRequest
            requestWithIdentifier:render ? args[2] : @"co-pymol-notification-test"
            content:content trigger:nil];
        [center addNotificationRequest:request withCompletionHandler:^(NSError *error) {
            if (error) fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
            exit(error ? 4 : 0);
        }];
    };
    [center getNotificationSettingsWithCompletionHandler:^(UNNotificationSettings *settings) {
        UNAuthorizationStatus authorization = settings.authorizationStatus;
        if (status) {
            NSString *name = @"unknown";
            switch (authorization) {
                case UNAuthorizationStatusNotDetermined: name = @"not_determined"; break;
                case UNAuthorizationStatusDenied: name = @"denied"; break;
                case UNAuthorizationStatusAuthorized: name = @"authorized"; break;
                case UNAuthorizationStatusProvisional: name = @"provisional"; break;
                default: break;
            }
            NSDictionary *result = @{
                @"authorization": name,
                @"alerts_enabled": @(settings.alertSetting == UNNotificationSettingEnabled),
                @"sounds_enabled": @(settings.soundSetting == UNNotificationSettingEnabled),
                @"notification_center_enabled": @(settings.notificationCenterSetting == UNNotificationSettingEnabled)
            };
            NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:nil];
            puts([[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding].UTF8String);
            exit(0);
        }
        if (authorization == UNAuthorizationStatusAuthorized ||
            authorization == UNAuthorizationStatusProvisional) {
            post();
        } else if (setup && authorization == UNAuthorizationStatusNotDetermined) {
            [center requestAuthorizationWithOptions:(UNAuthorizationOptionAlert | UNAuthorizationOptionSound)
                completionHandler:^(BOOL granted, NSError *error) {
                    if (!granted || error) {
                        fprintf(stderr, "Allow Blender Renders in System Settings > Notifications. %s\n",
                                error ? error.localizedDescription.UTF8String : "");
                        exit(3);
                    }
                    post();
                }];
        } else {
            // Detached render workers must never open permission prompts.
            fprintf(stderr, "Notifications need permission. Run co-pymol notifications setup; "
                            "if denied, allow Blender Renders in System Settings > Notifications.\n");
            exit(3);
        }
    }];
}

- (void)userNotificationCenter:(UNUserNotificationCenter *)center
      willPresentNotification:(UNNotification *)notification
        withCompletionHandler:(void (^)(UNNotificationPresentationOptions))completionHandler {
    completionHandler(UNNotificationPresentationOptionBanner | UNNotificationPresentationOptionList |
                      UNNotificationPresentationOptionSound);
}

- (void)userNotificationCenter:(UNUserNotificationCenter *)center
 didReceiveNotificationResponse:(UNNotificationResponse *)response
         withCompletionHandler:(void (^)(void))completionHandler {
    BOOL reveal = [response.actionIdentifier isEqualToString:@"show-in-finder"] ||
                  [response.actionIdentifier isEqualToString:UNNotificationDefaultActionIdentifier];
    // Retain the action used by notifications delivered by the older helper.
    BOOL folderOnly = [response.actionIdentifier isEqualToString:@"show-folder"];
    BOOL open = folderOnly || [response.actionIdentifier isEqualToString:@"open-image"];
    NSDictionary *info = response.notification.request.content.userInfo;
    // Setup/test alerts have no file target and need no Finder action.
    if (!info[@"image"] && !info[@"folder"]) {
        dispatch_async(dispatch_get_main_queue(), ^{
            completionHandler();
            [NSApp terminate:nil];
        });
        return;
    }
    dispatch_async(dispatch_get_main_queue(), ^{
        if (reveal || open) {
            NSURL *url = existingTarget(info[@"image"], info[@"folder"], folderOnly);
            if (reveal && url && !url.hasDirectoryPath) {
                [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[url]];
            } else if (!url || ![NSWorkspace.sharedWorkspace openURL:url]) {
                NSURL *fallback = existingTarget(nil, info[@"folder"], YES);
                if (!fallback || ![NSWorkspace.sharedWorkspace openURL:fallback]) {
                    NSAlert *alert = [NSAlert new];
                    alert.messageText = @"The saved render could not be opened";
                    alert.informativeText = info[@"folder"] ?: @"The files may have moved.";
                    [alert runModal];
                }
            }
        }
        completionHandler();
        [NSApp terminate:nil];
    });
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        // Exercise the same target selection used by a click without posting
        // notifications or requiring a graphical session.
        if (argc == 4 && strcmp(argv[1], "--resolve") == 0) {
            NSURL *url = existingTarget(@(argv[2]), @(argv[3]), NO);
            if (!url) return 2;
            puts(url.path.UTF8String);
            return 0;
        }
        NSApplication *app = NSApplication.sharedApplication;
        __attribute__((objc_precise_lifetime)) RenderNotifier *delegate = [RenderNotifier new];
        app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
