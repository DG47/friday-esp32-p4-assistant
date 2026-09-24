idf_component_register(
  SRCS "main.c" "friday_audio.c" "friday_state.c" "friday_ui.c" "friday_wifi.c"
  INCLUDE_DIRS "."
  REQUIRES esp_codec_dev esp_event esp_http_server esp_netif esp_timer esp_wifi nvs_flash
)
