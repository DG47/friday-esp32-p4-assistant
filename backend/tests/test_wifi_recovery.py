"""Exercise the production C recovery/event functions with a deterministic fake clock.

The extracted section is compiled verbatim; the harness replaces ESP side effects,
not the retry policy. No board, credentials, networking, or audio is accessed.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class WifiRecoveryTests(unittest.TestCase):
    def test_outage_preserves_settings_and_event_loop_retries(self):
        source = (ROOT / 'firmware/main/friday_wifi.c').read_text()
        start = source.find('/* Station recovery')
        if start < 0:
            start = source.index('static void reset_credentials_task')
        section = source[start:source.index('esp_err_t friday_wifi_start(void)', start)]
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stddef.h>
#include <string.h>
typedef int esp_err_t;
typedef int esp_event_base_t;
#define ESP_OK 0
#define WIFI_EVENT 1
#define IP_EVENT 2
#define FRIDAY_WIFI_RECOVERY_EVENT 3
#define FRIDAY_WIFI_RETRY_EVENT 1
#define WIFI_EVENT_STA_START 1
#define WIFI_EVENT_STA_DISCONNECTED 2
#define IP_EVENT_STA_GOT_IP 3
#define FRIDAY_WIFI_CONNECTED_BIT 1
#define FRIDAY_WIFI_MAX_RETRY_SECONDS 30
#define FRIDAY_MAX_RETRIES 5
#define IPSTR "%u"
#define IP2STR(p) (*(p))
#define TAG "test"
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define pdMS_TO_TICKS(x) (x)
#define pdPASS 1
static bool has_credentials=true;
static unsigned int retry_count;
static int retry_timer=1, wifi_events=1;
static unsigned int bits, connects, erased, restarted, posted;
static int connect_result, post_result;
static uint64_t delay_us;
static bool timer_active;
static char ui[32];
typedef struct { struct { unsigned int ip; } ip_info; } ip_event_got_ip_t;
static const char *esp_err_to_name(int x) { (void)x; return "synthetic"; }
static int esp_wifi_connect(void) { connects++; return connect_result; }
static int esp_timer_stop(int t) { (void)t;timer_active=false;return 0; }
static int esp_timer_start_once(int t,uint64_t delay) { assert(t==1);assert(!timer_active);timer_active=true;delay_us=delay;return 0; }
static int esp_event_post(int b,int id,void *data,size_t n,int wait) { assert(b==3 && id==1 && data==NULL && n==0 && wait==0);posted++;return post_result; }
static unsigned int xEventGroupGetBits(int group) { assert(group==1);return bits; }
static void xEventGroupClearBits(int group,unsigned int mask) { assert(group==1);bits &= ~mask; }
static void xEventGroupSetBits(int group,unsigned int mask) { assert(group==1);bits |= mask; }
static void friday_ui_set_network(const char *title,const char *detail) { (void)detail;snprintf(ui,sizeof(ui),"%s",title); }
/* Baseline destructive branch is deliberately executable, so it fails behavior. */
static void clear_credentials(void) { erased++; }
static void esp_restart(void) { restarted++; }
static void vTaskDelay(int n) { (void)n; }
static int xTaskCreate(void (*fn)(void*),const char *name,int stack,void *arg,int priority,void *handle) { (void)name;(void)stack;(void)priority;(void)handle;fn(arg);return pdPASS; }
'''
        tests = r'''
int main(void) {
    wifi_event_handler(NULL,WIFI_EVENT,WIFI_EVENT_STA_START,NULL);
    assert(connects==1);
    const unsigned int expected[]={1,2,4,8,16,30,30,30,30};
    for(unsigned int i=0;i<100;i++) {
        wifi_event_handler(NULL,WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,NULL);
        assert(has_credentials && erased==0 && restarted==0);
        assert(!(bits & FRIDAY_WIFI_CONNECTED_BIT));
        assert(timer_active);
        assert(delay_us==(uint64_t)expected[i<9?i:8]*1000000);
        timer_active=false;
        wifi_retry_timer(NULL); assert(posted==i+1);
        wifi_event_handler(NULL,FRIDAY_WIFI_RECOVERY_EVENT,FRIDAY_WIFI_RETRY_EVENT,NULL);
        assert(connects==i+2);
    }
    /* Successful IP resets backoff and cancels queued timers. */
    ip_event_got_ip_t event={.ip_info={.ip=42}};
    wifi_event_handler(NULL,IP_EVENT,IP_EVENT_STA_GOT_IP,&event);
    assert(bits & FRIDAY_WIFI_CONNECTED_BIT);assert(!timer_active);assert(retry_count==0);
    unsigned int previous=connects;
    wifi_event_handler(NULL,FRIDAY_WIFI_RECOVERY_EVENT,FRIDAY_WIFI_RETRY_EVENT,NULL);
    assert(connects==previous); /* already queued stale retry */
    wifi_event_handler(NULL,WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,NULL);
    assert(delay_us==1000000);
    /* Immediate connect errors retain a future retry even without DISCONNECTED. */
    timer_active=false;connect_result=-1;
    wifi_event_handler(NULL,FRIDAY_WIFI_RECOVERY_EVENT,FRIDAY_WIFI_RETRY_EVENT,NULL);
    assert(timer_active && delay_us==2000000);
    /* Event queue saturation does not permanently strand the one-shot timer. */
    timer_active=false;post_result=-1;wifi_retry_timer(NULL);
    assert(timer_active && delay_us==30000000);
    assert(erased==0 && restarted==0 && has_credentials);
    /* A queued recovery event cannot connect a device in provisioning mode. */
    has_credentials=false;previous=connects;
    wifi_event_handler(NULL,FRIDAY_WIFI_RECOVERY_EVENT,FRIDAY_WIFI_RETRY_EVENT,NULL);
    wifi_event_handler(NULL,WIFI_EVENT,WIFI_EVENT_STA_START,NULL);
    wifi_event_handler(NULL,WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,NULL);
    assert(connects==previous && erased==0 && restarted==0);
    puts("100 outages, capped retry, reset, stale event, connect failure, queue saturation: pass");
}
'''
        compiler = shutil.which('cc')
        self.assertIsNotNone(compiler, 'C compiler required for firmware recovery regression')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'recovery.c').write_text(harness + section + tests)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Wno-unused-function',
                            str(path/'recovery.c'), '-o', str(path/'recovery')], check=True, capture_output=True)
            result = subprocess.run([str(path/'recovery')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
