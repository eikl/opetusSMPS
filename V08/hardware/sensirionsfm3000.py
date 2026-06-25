"""
Sensirion I2C SFM3000 Python Library

This is a Python port of the Sensirion SFM3000 Arduino library
(https://github.com/Sensirion/arduino-i2c-sfm3000)

Uses I2C interface to communicate with SFM3000/SFM3200/SFM3300/SFM3400 flow sensors.

I2C Protocol based on:
Sensirion I2C Functional Description for SFM3xxx series

Commands:
- 0x1000: Start continuous measurement (returns 2 bytes + CRC)
- 0x31AE: Read Serial Number bits 31:16 (returns 2 bytes + CRC)
- 0x31AF: Read Serial Number bits 15:0 (returns 2 bytes + CRC)
- 0x2000: Soft reset

CRC-8: polynomial x^8 + x^5 + x^4 + 1 (0x31), init=0x00

Copyright (c) 2021, Sensirion AG (original Arduino library)
Copyright (c) 2025, Python port
All rights reserved.

BSD-3-Clause License
"""

import time
from typing import Optional, Tuple
from enum import IntEnum

try:
    import smbus2
except ImportError:
    smbus2 = None


# Default I2C address for SFM3000 (7-bit address = 64 = 0x40)
SFM3000_I2C_ADDRESS_0 = 0x40


class SensirionI2CError(Exception):
    """Exception raised for Sensirion I2C communication errors."""
    pass


class CrcError(SensirionI2CError):
    """Exception raised when CRC check fails."""
    pass


class NackError(SensirionI2CError):
    """Exception raised when sensor does not acknowledge (no valid data ready)."""
    pass


def generate_crc(data: bytes, init: int = 0x00, polynomial: int = 0x31) -> int:
    """
    Calculate CRC-8 checksum as per SFM3xxx datasheet.
    
    CRC-8 standard based on generator polynomial: x^8 + x^5 + x^4 + 1 (0x31)
    
    Args:
        data: Bytes to calculate CRC for (typically 2 bytes)
        init: Initial CRC value (0x00 for SFM3xxx)
        polynomial: CRC polynomial (0x31 for Sensirion sensors)
    
    Returns:
        8-bit CRC checksum
    """
    crc = init
    
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ polynomial) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    
    return crc


class SensirionI2CSfm3000:
    """
    Python driver for Sensirion SFM3xxx flow sensor via I2C.
    
    This class provides methods to communicate with SFM3000/SFM3200/SFM3300/SFM3400 
    mass flow sensors using the I2C protocol as specified in the Sensirion 
    I2C Functional Description document.
    
    I2C Protocol:
    - Write command: S + I2CAdr + W + ACK + Cmd[15:8] + ACK + Cmd[7:0] + ACK + P
    - Read response: S + I2CAdr + R + ACK + Data[15:8] + ACK + Data[7:0] + ACK + CRC + NACK + P
    
    Example usage:
        >>> from sensirionsfm3000 import SensirionI2CSfm3000, SFM3000_I2C_ADDRESS_0
        >>> sfm = SensirionI2CSfm3000()
        >>> sfm.begin(i2c_bus=1, i2c_address=SFM3000_I2C_ADDRESS_0)
        >>> serial = sfm.read_serial_number()
        >>> print(f"Serial Number: {serial}")
        >>> sfm.start_continuous_measurement()
        >>> flow = sfm.read_measurement(scaling_factor=140.0, offset=32000.0)
        >>> print(f"Flow: {flow}")
    """
    
    # I2C Commands as per datasheet
    CMD_START_CONTINUOUS_MEASUREMENT = 0x1000  # Start flow measurement (returns 2 bytes)
    CMD_START_TEMP_MEASUREMENT = 0x1001        # Start temperature measurement
    CMD_READ_SCALE_FACTOR = 0x30DE             # Read scale factor
    CMD_READ_OFFSET = 0x30DF                   # Read offset
    CMD_READ_ARTICLE_NUMBER_HIGH = 0x31E3      # Read article number bits 31:16
    CMD_READ_ARTICLE_NUMBER_LOW = 0x31E4       # Read article number bits 15:0
    CMD_READ_SERIAL_NUMBER_HIGH = 0x31AE       # Read serial number bits 31:16 
    CMD_READ_SERIAL_NUMBER_LOW = 0x31AF        # Read serial number bits 15:0
    CMD_SOFT_RESET = 0x2000                    # Soft reset command
    
    # Default calibration values (from SFM3000 datasheet)
    DEFAULT_SCALING_FACTOR = 140.0
    DEFAULT_OFFSET = 32000.0
    
    def __init__(self):
        """Initialize the SensirionI2CSfm3000 instance."""
        self._i2c_bus: Optional[smbus2.SMBus] = None
        self._i2c_address: int = 0x00
        self._owns_bus: bool = False
    
    def begin(self, i2c_bus, i2c_address: int = SFM3000_I2C_ADDRESS_0):
        """
        Initialize the SensirionI2CSfm3000 class.
        
        Args:
            i2c_bus: Either an SMBus object or an integer representing the I2C bus number.
                     If an integer is provided, a new SMBus instance will be created.
            i2c_address: I2C address of your sensor (7-bit address).
                        Default is 0x40 (SFM3000_I2C_ADDRESS_0)
        """
        if smbus2 is None:
            raise ImportError("smbus2 is required. Install it with: pip install smbus2")
        
        if isinstance(i2c_bus, int):
            self._i2c_bus = smbus2.SMBus(i2c_bus)
            self._owns_bus = True
        else:
            self._i2c_bus = i2c_bus
            self._owns_bus = False
        
        self._i2c_address = i2c_address
    
    def close(self):
        """Close the I2C bus if we own it."""
        if self._owns_bus and self._i2c_bus is not None:
            self._i2c_bus.close()
            self._i2c_bus = None
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False
    
    def _write_command(self, command: int) -> None:
        """
        Write a 16-bit command to the sensor.
        
        I2C sequence: S + I2CAdr + W + ACK + Cmd[15:8] + ACK + Cmd[7:0] + ACK + P
        
        Args:
            command: 16-bit command to send
        """
        if self._i2c_bus is None:
            raise SensirionI2CError("I2C bus not initialized. Call begin() first.")
        
        # Split 16-bit command into two bytes (MSB first)
        msb = (command >> 8) & 0xFF
        lsb = command & 0xFF
        
        # Write both command bytes using i2c_rdwr for proper protocol
        from smbus2 import i2c_msg
        write_msg = i2c_msg.write(self._i2c_address, [msb, lsb])
        self._i2c_bus.i2c_rdwr(write_msg)
    
    def _read_word_with_crc(self) -> int:
        """
        Read a 16-bit word (2 data bytes + 1 CRC byte) from sensor.
        
        I2C sequence: S + I2CAdr + R + ACK + Data[15:8] + ACK + Data[7:0] + ACK + CRC + NACK + P
        
        The sensor returns data in format: [MSB, LSB, CRC]
        CRC is calculated over the 2 data bytes.
        
        Returns:
            16-bit unsigned value
        
        Raises:
            CrcError: If CRC validation fails
            NackError: If sensor does not acknowledge (no valid data ready)
        """
        if self._i2c_bus is None:
            raise SensirionI2CError("I2C bus not initialized. Call begin() first.")
        
        # Read 3 bytes: MSB, LSB, CRC
        # Use i2c_rdwr for pure read without sending register byte
        try:
            from smbus2 import i2c_msg
            read_msg = i2c_msg.read(self._i2c_address, 3)
            self._i2c_bus.i2c_rdwr(read_msg)
            raw_data = list(read_msg)
        except OSError as e:
            raise NackError(f"Sensor did not acknowledge read request: {e}")
        
        msb = raw_data[0]
        lsb = raw_data[1]
        crc_received = raw_data[2]
        
        # Validate CRC over the 2 data bytes
        data_bytes = bytes([msb, lsb])
        crc_calculated = generate_crc(data_bytes)
        
        if crc_calculated != crc_received:
            raise CrcError(
                f"CRC mismatch: calculated 0x{crc_calculated:02X}, "
                f"received 0x{crc_received:02X} for data {data_bytes.hex()}"
            )
        
        # Combine to 16-bit value (big endian)
        return (msb << 8) | lsb
    
    def _read_2_words_with_crc(self) -> Tuple[int, int]:
        """
        Read two 16-bit words (4 data bytes + 2 CRC bytes) from sensor.
        
        Format: [MSB1, LSB1, CRC1, MSB2, LSB2, CRC2]
        
        Returns:
            Tuple of two 16-bit unsigned values
        
        Raises:
            CrcError: If CRC validation fails
        """
        if self._i2c_bus is None:
            raise SensirionI2CError("I2C bus not initialized. Call begin() first.")
        
        # Read 6 bytes: MSB1, LSB1, CRC1, MSB2, LSB2, CRC2
        # Use i2c_rdwr for pure read without sending register byte
        try:
            from smbus2 import i2c_msg
            read_msg = i2c_msg.read(self._i2c_address, 6)
            self._i2c_bus.i2c_rdwr(read_msg)
            raw_data = list(read_msg)
        except OSError as e:
            raise NackError(f"Sensor did not acknowledge read request: {e}")
        
        # First word
        msb1, lsb1, crc1 = raw_data[0], raw_data[1], raw_data[2]
        data1 = bytes([msb1, lsb1])
        crc1_calc = generate_crc(data1)
        if crc1_calc != crc1:
            raise CrcError(f"CRC1 mismatch: calc 0x{crc1_calc:02X}, recv 0x{crc1:02X}")
        
        # Second word
        msb2, lsb2, crc2 = raw_data[3], raw_data[4], raw_data[5]
        data2 = bytes([msb2, lsb2])
        crc2_calc = generate_crc(data2)
        if crc2_calc != crc2:
            raise CrcError(f"CRC2 mismatch: calc 0x{crc2_calc:02X}, recv 0x{crc2:02X}")
        
        word1 = (msb1 << 8) | lsb1
        word2 = (msb2 << 8) | lsb2
        
        return word1, word2
    
    def start_continuous_measurement(self) -> None:
        """
        Start continuous measurements.
        
        This command (0x1000) starts continuous flow measurement. Measurement results
        are continuously updated until stopped by sending any other command or reset.
        
        After this command, measurements can be read continuously using read_measurement().
        
        Note: It is recommended to send this command before every read to handle
        unexpected sensor resets (see datasheet section 7).
        
        Raises:
            SensirionI2CError: If communication fails
        """
        self._write_command(self.CMD_START_CONTINUOUS_MEASUREMENT)
        time.sleep(0.001)  # 1ms delay
    
    def read_measurement_raw(self) -> int:
        """
        Read raw flow measurement value.
        
        Should be called after start_continuous_measurement(). The measurement result
        can be read at most every 0.5ms. This method sends the start measurement command
        before reading to ensure reliable operation (recommended by datasheet).
        
        Returns:
            The raw 16-bit flow measurement result (bits 1:0 are always zero).
            Convert to physical value using: flow = (raw - offset) / scale_factor
        
        Raises:
            SensirionI2CError: If communication fails
            CrcError: If CRC validation fails
            NackError: If no valid measurement is ready yet
        """
        # Send start measurement command before each read for reliability
        # (handles unexpected sensor resets - see datasheet section 7)
        self._write_command(self.CMD_START_CONTINUOUS_MEASUREMENT)
        time.sleep(0.0005)  # 0.5ms minimum between command and read
        
        return self._read_word_with_crc()
    
    def read_measurement(
        self,
        scaling_factor: float = DEFAULT_SCALING_FACTOR,
        offset: float = DEFAULT_OFFSET
    ) -> float:
        """
        Read measurement and return calibrated flow value.
        
        Formula: flow [slm] = (measured_value - offset) / scale_factor
        
        Args:
            scaling_factor: Scaling factor from datasheet. Default is 140.0 for SFM3000.
            offset: Flow offset from datasheet. Default is 32000.0 for SFM3000.
        
        Returns:
            Calibrated flow value in slm (standard liters per minute)
        
        Raises:
            SensirionI2CError: If communication fails
            CrcError: If CRC validation fails
            NackError: If no valid measurement is ready yet
        """
        flow_raw = self.read_measurement_raw()
        flow = (float(flow_raw) - offset) / scaling_factor
        return flow
    
    def read_serial_number(self) -> int:
        """
        Read the 32-bit serial number of the sensor.
        
        As per datasheet, the full serial number requires two commands:
        - 0x31AE: Returns bits 31:16 (high word)
        - 0x31AF: Returns bits 15:0 (low word)
        
        Returns:
            32-bit unique serial number
        
        Raises:
            SensirionI2CError: If communication fails
            CrcError: If CRC validation fails
        """
        # Read high word (bits 31:16)
        self._write_command(self.CMD_READ_SERIAL_NUMBER_HIGH)
        time.sleep(0.001)  # 1ms delay
        high_word = self._read_word_with_crc()
        
        # Read low word (bits 15:0)
        self._write_command(self.CMD_READ_SERIAL_NUMBER_LOW)
        time.sleep(0.001)  # 1ms delay
        low_word = self._read_word_with_crc()
        
        # Combine to 32-bit serial number
        serial_number = (high_word << 16) | low_word
        return serial_number
    
    def read_article_number(self) -> int:
        """
        Read the 32-bit article/product number of the sensor.
        
        Requires two commands:
        - 0x31E3: Returns bits 31:16 (high word)
        - 0x31E4: Returns bits 15:0 (low word)
        
        Returns:
            32-bit article/product number
        
        Raises:
            SensirionI2CError: If communication fails
            CrcError: If CRC validation fails
        """
        # Read high word (bits 31:16)
        self._write_command(self.CMD_READ_ARTICLE_NUMBER_HIGH)
        time.sleep(0.001)
        high_word = self._read_word_with_crc()
        
        # Read low word (bits 15:0)
        self._write_command(self.CMD_READ_ARTICLE_NUMBER_LOW)
        time.sleep(0.001)
        low_word = self._read_word_with_crc()
        
        # Combine to 32-bit article number
        article_number = (high_word << 16) | low_word
        return article_number
    
    def read_scale_factor(self) -> int:
        """
        Read the flow scale factor from the sensor.
        
        Returns:
            Scale factor value (16-bit signed)
        """
        self._write_command(self.CMD_READ_SCALE_FACTOR)
        time.sleep(0.001)
        value = self._read_word_with_crc()
        # Convert to signed if needed
        if value >= 0x8000:
            value -= 0x10000
        return value
    
    def read_offset(self) -> int:
        """
        Read the flow offset from the sensor.
        
        Returns:
            Offset value (16-bit signed)
        """
        self._write_command(self.CMD_READ_OFFSET)
        time.sleep(0.001)
        value = self._read_word_with_crc()
        # Convert to signed if needed
        if value >= 0x8000:
            value -= 0x10000
        return value
    
    def soft_reset(self) -> None:
        """
        Perform a soft reset of the sensor.
        
        Forces a sensor reset without switching power off/on. The sensor
        reinitializes from non-volatile memory and starts operating according
        to those settings.
        
        Note: If the sensor is locked up (see datasheet section 7), a hard reset
        (power cycle) may be required.
        """
        self._write_command(self.CMD_SOFT_RESET)
        time.sleep(0.1)  # Wait for reset to complete
    
    def stop_continuous_measurement(self) -> None:
        """
        Stop continuous measurements by issuing a soft reset.
        
        This stops the measurement mode and returns sensor to idle.
        """
        self.soft_reset()


# Convenience function to match Arduino example usage
def error_to_string(error: Exception) -> str:
    """
    Convert an error to a human-readable string.
    
    Args:
        error: The exception to convert
    
    Returns:
        Human-readable error string
    """
    if isinstance(error, CrcError):
        return f"CRC Error: {str(error)}"
    elif isinstance(error, NackError):
        return f"NACK Error (no data ready): {str(error)}"
    elif isinstance(error, SensirionI2CError):
        return f"I2C Error: {str(error)}"
    elif isinstance(error, OSError):
        return f"OS Error: {str(error)}"
    else:
        return str(error)


# Example usage
if __name__ == "__main__":
    import sys
    
    # Default scaling factor and offset for SFM3000 (from datasheet)
    scaling_factor = 140.0
    offset = 32000.0
    
    print("Sensirion SFM3xxx Flow Sensor - Python Example")
    print("=" * 50)
    print("I2C Protocol based on Sensirion I2C Functional Description")
    print()
    
    try:
        sfm = SensirionI2CSfm3000()
        
        # Initialize with I2C bus 1 (typical for Raspberry Pi)
        # Change to appropriate bus number for your system
        sfm.begin(i2c_bus=1, i2c_address=SFM3000_I2C_ADDRESS_0)
        print(f"Connected to I2C bus 1, address 0x{SFM3000_I2C_ADDRESS_0:02X}")
        
        # Read serial number (using extended command set: 0x31AE + 0x31AF)
        try:
            serial_number = sfm.read_serial_number()
            print(f"Serial Number: {serial_number} (0x{serial_number:08X})")
        except Exception as e:
            print(f"Error reading serial number: {error_to_string(e)}")
            sys.exit(1)
        
        # Optionally read article number
        try:
            article_number = sfm.read_article_number()
            print(f"Article Number: {article_number} (0x{article_number:08X})")
        except Exception as e:
            print(f"Note: Could not read article number: {error_to_string(e)}")
        
        # Optionally read scale factor and offset from sensor
        try:
            sf = sfm.read_scale_factor()
            off = sfm.read_offset()
            print(f"Scale Factor (from sensor): {sf}")
            print(f"Offset (from sensor): {off}")
        except Exception as e:
            print(f"Note: Using default scale factor ({scaling_factor}) and offset ({offset})")
        
        # Start continuous measurement
        try:
            sfm.start_continuous_measurement()
            print("\nStarted continuous measurement (command 0x1000)")
        except Exception as e:
            print(f"Error starting measurement: {error_to_string(e)}")
            sys.exit(1)
        
        # Wait for first valid measurement (first one after reset may be invalid)
        time.sleep(0.1)
        
        # Read measurements in a loop
        print("\nReading flow measurements (Ctrl+C to stop)...")
        print("-" * 30)
        
        try:
            while True:
                time.sleep(0.1)  # 100ms between readings
                
                try:
                    flow = sfm.read_measurement(scaling_factor, offset)
                    print(f"Flow: {flow:+8.2f} slm")
                except NackError:
                    # No valid measurement ready yet, skip
                    print("Waiting for valid measurement...")
                except CrcError as e:
                    print(f"CRC Error: {e}")
                except Exception as e:
                    print(f"Error reading measurement: {error_to_string(e)}")
        
        except KeyboardInterrupt:
            print("\nStopping...")
        
        finally:
            sfm.close()
            print("Sensor connection closed")
    
    except ImportError as e:
        print(f"Import error: {e}")
        print("Make sure smbus2 is installed: pip install smbus2")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {error_to_string(e)}")
        sys.exit(1)
