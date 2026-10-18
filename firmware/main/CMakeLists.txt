# Compile the same immutable assets used by the native renderer.
execute_process(COMMAND python3 "${CMAKE_CURRENT_SOURCE_DIR}/../../scripts/weather_assets_c.py"
  "${CMAKE_CURRENT_BINARY_DIR}/weather_assets.c" COMMAND_ERROR_IS_FATAL ANY)
file(GLOB WEATHER_ASSETS "${CMAKE_CURRENT_SOURCE_DIR}/weather_assets/*.rgb565")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${WEATHER_ASSETS}
  "${CMAKE_CURRENT_SOURCE_DIR}/../../scripts/weather_assets_c.py")
idf_component_register(
  SRCS "${CMAKE_CURRENT_BINARY_DIR}/weather_assets.c" "main.c" "friday_audio.c" "friday_echo.c" "friday_error_scene.c" "friday_backend.c" "friday_state.c" "friday_execution_stop.c" "friday_ui.c" "friday_presence.c" "friday_artwork.c" "friday_media_overlay.c" "friday_media_guard.c" "friday_experience_guard.c" "friday_display_text.c" "friday_vad.c" "friday_wifi.c"
  INCLUDE_DIRS "."
  REQUIRES esp_codec_dev esp_event esp_http_server esp_netif esp_timer esp_wifi
           esp_websocket_client json nvs_flash mbedtls esp-sr
  EMBED_TXTFILES "friday_backend_ca.pem"
)
