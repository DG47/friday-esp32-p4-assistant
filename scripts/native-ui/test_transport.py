"""Exercise the actual firmware receive_fragment function, without ESP-IDF transport."""
from pathlib import Path
import subprocess,tempfile
r=Path(__file__).resolve().parents[2]
s=(r/'firmware/main/friday_backend.c').read_text();start=s.index('static void receive_fragment(');end=s.index('static void websocket_event_handler',start)
source='''#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "friday_artwork.h"
#define ESP_LOGW(...) ((void)0)
typedef struct {int payload_len,payload_offset,data_len;const char *data_ptr;} esp_websocket_event_data_t;
static char *message_buffer;static size_t message_size,message_offset;static int completed;static size_t received;
static void handle_message(const char *s){completed++;received=strlen(s);}
'''+s[start:end]+'''
int main(void){
 char *data=malloc(FRIDAY_MEDIA_MESSAGE_MAX);memset(data,'x',FRIDAY_MEDIA_MESSAGE_MAX);
 esp_websocket_event_data_t e={.payload_len=FRIDAY_MEDIA_MESSAGE_MAX,.data_ptr=data};
 for(int offset=0;offset<FRIDAY_MEDIA_MESSAGE_MAX;offset+=12288){e.payload_offset=offset;e.data_len=FRIDAY_MEDIA_MESSAGE_MAX-offset<12288?FRIDAY_MEDIA_MESSAGE_MAX-offset:12288;receive_fragment(&e);}
 assert(completed==1&&received==FRIDAY_MEDIA_MESSAGE_MAX&&!message_buffer);
 e.payload_len=FRIDAY_MEDIA_MESSAGE_MAX+1;e.payload_offset=0;e.data_len=1;receive_fragment(&e);assert(completed==1&&!message_buffer);
 e.payload_len=100;e.payload_offset=0;e.data_len=20;receive_fragment(&e);assert(message_buffer);e.payload_offset=40;receive_fragment(&e);assert(!message_buffer);
 e.payload_offset=0;e.data_len=20;receive_fragment(&e);e.payload_len=101;e.payload_offset=20;receive_fragment(&e);assert(!message_buffer);
 e.payload_len=100;e.payload_offset=0;e.data_len=101;receive_fragment(&e);assert(!message_buffer);
 e.payload_len=-1;receive_fragment(&e);assert(!message_buffer);free(data);puts("Actual WS assembler limit/fragment/order/reset: PASS");return 0;}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'transport.c';p.write_text(source);binary=Path(d)/'test'
 # Only cJSON's type declarations are needed for the production artwork limit header.
 import sys
 cjson=Path(sys.argv[1]);subprocess.run(['cc','-std=c11','-Wall','-Wextra','-I'+str(r/'firmware/main'),'-I'+str(cjson),str(p),'-o',str(binary)],check=True);subprocess.run([str(binary)],check=True)
