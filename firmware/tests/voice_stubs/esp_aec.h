#pragma once
#include <stdint.h>
typedef struct {int ignored;} aec_handle_t;
#define AEC_MODE_SR_LOW_COST 0
aec_handle_t *aec_create(int,int,int,int);
void aec_destroy(aec_handle_t*);
int aec_get_chunksize(aec_handle_t*);
void aec_process(aec_handle_t*,int16_t*,int16_t*,int16_t*);
