from tado_local.homekit_uuids import (
    get_service_name,
    get_characteristic_name,
    get_characteristic_value_name,
    enhance_accessory_data,
    add_tado_specific_info,
)


class TestHomekitUUIDsGetServiceName:
    def test_get_service_name_apple_standard(self):
        """Test retrieving standard Apple HomeKit service names."""
        uuid = "0000003E-0000-1000-8000-0026BB765291"
        assert get_service_name(uuid) == "AccessoryInformation"

        uuid = "0000004A-0000-1000-8000-0026BB765291"
        assert get_service_name(uuid) == "Thermostat"

    def test_get_service_name_case_insensitive(self):
        """Test that service name lookup is case-insensitive."""
        uuid_lower = "0000003e-0000-1000-8000-0026bb765291"
        uuid_upper = "0000003E-0000-1000-8000-0026BB765291"
        assert get_service_name(uuid_lower) == get_service_name(uuid_upper)

    def test_get_service_name_tado_custom(self):
        """Test retrieving Tado custom service names."""
        uuid = "E44673A0-247B-4360-8A76-DB9DA69C0100"
        assert get_service_name(uuid) == "TadoProprietaryService"

    def test_get_service_name_unknown_returns_uuid(self):
        """Test that unknown UUIDs are returned as-is."""
        unknown_uuid = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"
        assert get_service_name(unknown_uuid) == unknown_uuid.upper()


class TestHomekitUUIDsGetCharacteristicName:
    def test_get_characteristic_name_apple_standard(self):
        """Test retrieving standard Apple HomeKit characteristic names."""
        uuid = "00000011-0000-1000-8000-0026BB765291"
        assert get_characteristic_name(uuid) == "CurrentTemperature"

        uuid = "00000035-0000-1000-8000-0026BB765291"
        assert get_characteristic_name(uuid) == "TargetTemperature"

    def test_get_characteristic_name_case_insensitive(self):
        """Test that characteristic name lookup is case-insensitive."""
        uuid_lower = "00000011-0000-1000-8000-0026bb765291"
        uuid_upper = "00000011-0000-1000-8000-0026BB765291"
        assert get_characteristic_name(uuid_lower) == get_characteristic_name(uuid_upper)

    def test_get_characteristic_name_tado_custom(self):
        """Test retrieving Tado custom characteristic names."""
        uuid = "E44673A0-247B-4360-8A76-DB9DA69C0101"
        assert get_characteristic_name(uuid) == "TadoProprietaryControl"

    def test_get_characteristic_name_unknown_returns_uuid(self):
        """Test that unknown UUIDs are returned as-is."""
        unknown_uuid = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"
        assert get_characteristic_name(unknown_uuid) == unknown_uuid.upper()


class TestHomekitUUIDsGetCharacteristicValueName:
    def test_get_characteristic_value_name_heating_cooling(self):
        """Test value name retrieval for heating/cooling states."""
        assert get_characteristic_value_name("CurrentHeatingCoolingState", 0) == "Off"
        assert get_characteristic_value_name("CurrentHeatingCoolingState", 1) == "Heat"
        assert get_characteristic_value_name("CurrentHeatingCoolingState", 2) == "Cool"
        assert get_characteristic_value_name("CurrentHeatingCoolingState", 3) == "Auto"

    def test_get_characteristic_value_name_temperature_units(self):
        """Test value name retrieval for temperature display units."""
        assert get_characteristic_value_name("TemperatureDisplayUnits", 0) == "Celsius"
        assert get_characteristic_value_name("TemperatureDisplayUnits", 1) == "Fahrenheit"

    def test_get_characteristic_value_name_battery_status(self):
        """Test value name retrieval for battery status."""
        assert get_characteristic_value_name("StatusLowBattery", 0) == "Normal"
        assert get_characteristic_value_name("StatusLowBattery", 1) == "Low Battery"

    def test_get_characteristic_value_name_unknown_returns_string(self):
        """Test that unknown values are returned as strings."""
        result = get_characteristic_value_name("UnknownCharacteristic", 42)
        assert result == "42"

    def test_get_characteristic_value_name_unknown_value_returns_string(self):
        """Test that unknown values for known characteristics return string."""
        result = get_characteristic_value_name("CurrentHeatingCoolingState", 99)
        assert result == "99"


class TestHomekitUUIDsAddTadoSpecificInfo:
    def test_add_tado_specific_info_current_temperature(self):
        """Test Tado-specific temperature conversion."""
        enhanced_char = {}
        result = add_tado_specific_info(enhanced_char, "CurrentTemperature", 20.0)

        assert result["temperature_celsius"] == 20.0
        assert result["temperature_fahrenheit"] == 68.0

    def test_add_tado_specific_info_temperature_conversion_precision(self):
        """Test temperature conversion maintains 1 decimal precision."""
        enhanced_char = {}
        result = add_tado_specific_info(enhanced_char, "CurrentTemperature", 22.5)

        assert result["temperature_fahrenheit"] == 72.5

    def test_add_tado_specific_info_humidity(self):
        """Test Tado-specific humidity formatting."""
        enhanced_char = {}
        result = add_tado_specific_info(enhanced_char, "CurrentRelativeHumidity", 45)

        assert result["humidity_percent"] == "45%"

    def test_add_tado_specific_info_none_temperature(self):
        """Test that None temperature is not processed."""
        enhanced_char = {"value": None}
        result = add_tado_specific_info(enhanced_char, "CurrentTemperature", None)

        assert "temperature_celsius" not in result
        assert "temperature_fahrenheit" not in result

    def test_add_tado_specific_info_other_characteristic(self):
        """Test that non-Tado-specific characteristics are unmodified."""
        enhanced_char = {"value": "test"}
        result = add_tado_specific_info(enhanced_char, "SerialNumber", "ABC123")

        assert len(result) == 1
        assert result == {"value": "test"}


class TestHomekitUUIDsEnhanceAccessoryData:
    def test_enhance_accessory_data_basic_structure(self):
        """Test basic enhancement of accessory data structure."""
        accessories = [
            {
                "id": 1,
                "aid": 1,
                "serial_number": "ABC123",
                "services": []
            }
        ]

        result = enhance_accessory_data(accessories)

        assert len(result) == 1
        assert result[0]["aid"] == 1
        assert result[0]["serial_number"] == "ABC123"

    def test_enhance_accessory_data_with_temperature_service(self):
        """Test enhancement of temperature sensor service."""
        accessories = [
            {
                "aid": 1,
                "services": [
                    {
                        "type": "0000008A-0000-1000-8000-0026BB765291",
                        "iid": 10,
                        "characteristics": [
                            {
                                "type": "00000011-0000-1000-8000-0026BB765291",
                                "iid": 11,
                                "value": 20.5,
                                "perms": ["pr", "ev"],
                                "format": "float"
                            }
                        ]
                    }
                ]
            }
        ]

        result = enhance_accessory_data(accessories)
        service = result[0]["services"][0]

        assert service["type_name"] == "TemperatureSensor"
        char = service["characteristics"][0]
        assert char["type_name"] == "CurrentTemperature"
        assert char["value"] == 20.5
        assert char["value_name"] == "20.5"
        assert char["temperature_celsius"] == 20.5
        assert char["temperature_fahrenheit"] == 68.9

    def test_enhance_accessory_data_with_thermostat_states(self):
        """Test enhancement of thermostat with heating/cooling states."""
        accessories = [
            {
                "aid": 1,
                "services": [
                    {
                        "type": "0000004A-0000-1000-8000-0026BB765291",
                        "iid": 10,
                        "characteristics": [
                            {
                                "type": "0000000F-0000-1000-8000-0026BB765291",
                                "iid": 11,
                                "value": 1,
                                "perms": ["pr", "ev"]
                            },
                            {
                                "type": "00000033-0000-1000-8000-0026BB765291",
                                "iid": 12,
                                "value": 1,
                                "perms": ["pr", "pw", "ev"]
                            }
                        ]
                    }
                ]
            }
        ]

        result = enhance_accessory_data(accessories)
        chars = result[0]["services"][0]["characteristics"]

        assert chars[0]["type_name"] == "CurrentHeatingCoolingState"
        assert chars[0]["value_name"] == "Heat"
        assert chars[1]["type_name"] == "TargetHeatingCoolingState"
        assert chars[1]["value_name"] == "Heat"

    def test_enhance_accessory_data_with_multiple_accessories(self):
        """Test enhancement of multiple accessories."""
        accessories = [
            {"aid": 1, "services": []},
            {"aid": 2, "services": []},
            {"aid": 3, "services": []}
        ]

        result = enhance_accessory_data(accessories)

        assert len(result) == 3
        assert result[0]["aid"] == 1
        assert result[1]["aid"] == 2
        assert result[2]["aid"] == 3

    def test_enhance_accessory_data_with_constraints(self):
        """Test that min/max constraints are preserved."""
        accessories = [
            {
                "aid": 1,
                "services": [
                    {
                        "type": "0000004A-0000-1000-8000-0026BB765291",
                        "iid": 10,
                        "characteristics": [
                            {
                                "type": "00000035-0000-1000-8000-0026BB765291",
                                "iid": 11,
                                "value": 21,
                                "minValue": 5,
                                "maxValue": 35,
                                "minStep": 0.5,
                                "perms": ["pr", "pw", "ev"]
                            }
                        ]
                    }
                ]
            }
        ]

        result = enhance_accessory_data(accessories)
        char = result[0]["services"][0]["characteristics"][0]

        assert char["minValue"] == 5
        assert char["maxValue"] == 35
        assert char["minStep"] == 0.5

    def test_enhance_accessory_data_humidity_with_sensor(self):
        """Test humidity sensor enhancement."""
        accessories = [
            {
                "aid": 1,
                "services": [
                    {
                        "type": "00000082-0000-1000-8000-0026BB765291",
                        "iid": 10,
                        "characteristics": [
                            {
                                "type": "00000010-0000-1000-8000-0026BB765291",
                                "iid": 11,
                                "value": 55,
                                "perms": ["pr", "ev"]
                            }
                        ]
                    }
                ]
            }
        ]

        result = enhance_accessory_data(accessories)
        char = result[0]["services"][0]["characteristics"][0]

        assert char["type_name"] == "CurrentRelativeHumidity"
        assert char["humidity_percent"] == "55%"

    def test_enhance_accessory_data_empty_accessories_list(self):
        """Test enhancement with empty accessories list."""
        result = enhance_accessory_data([])
        assert result == []

    def test_enhance_accessory_data_preserves_perms(self):
        """Test that permissions are preserved in enhancement."""
        accessories = [
            {
                "aid": 1,
                "services": [
                    {
                        "type": "0000004A-0000-1000-8000-0026BB765291",
                        "iid": 10,
                        "characteristics": [
                            {
                                "type": "00000035-0000-1000-8000-0026BB765291",
                                "iid": 11,
                                "value": 21,
                                "perms": ["pr", "pw", "ev"]
                            }
                        ]
                    }
                ]
            }
        ]

        result = enhance_accessory_data(accessories)
        char = result[0]["services"][0]["characteristics"][0]

        assert char["perms"] == ["pr", "pw", "ev"]

