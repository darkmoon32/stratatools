#
# See the LICENSE file
#

import datetime
import struct
import time

import cartridge_pb2
import material

#
# CartridgeManager is used to create, encrypt and decrypt Stratasys cartridge
#
# Typical structure on the EEPROM
#        offset : len
#        0x00   : 0x08 - Canister serial number (double) (part of the key, written *on* the canister as S/N)
#        0x08   : 0x08 - Material type (double)
#        0x10   : 0x14 - Manufacturing lot (string)
#        0x24   : 0x02 - Version? (must be 1)
#        0x28   : 0x08 - Manufacturing date (date yymmddhhmmss)
#        0x30   : 0x08 - Use date (date yymmddhhmmss)
#        0x38   : 0x08 - Initial material quantity (double)
#        0x40   : 0x02 - Plain content CRC (uint16)
#        0x46   : 0x02 - Crypted content CRC (uint16)
#        0x48   : 0x08 - Key (unencrypted, 8 bytes)
#        0x50   : 0x02 - Key CRC (unencrypted, uint16)
#        0x58   : 0x08 - Current material quantity (double)
#        0x60   : 0x02 - Current material quantity crypted CRC (unencrypted, uint16)
#        0x62   : 0x02 - Current material quantity CRC (unencrypted, uint16)
#       ~~~~~~~~~~~~~
#       14 0x00: 0x48 - crypted/plaintext (start, len)
#       15 0x58: 0x10 - unknown, looks like DEX IV, but why?
#       16 0x48: 0x10 - ^

class Manager:
    def __init__(self, crypto, checksum):
        self.crypto = crypto
        self.checksum = checksum

    #
    # Encode a cartridge object into a data that can be burn onto a cartridge
    #
    def encode(self, machine_number, eeprom_uid, cartridge):
        cartridge_packed = self.pack(cartridge)
        cartridge_crypted = self.encrypt(machine_number, eeprom_uid, cartridge_packed)
        return cartridge_crypted

    #
    # Decode a eeprom to a cartridge object
    #
    def decode(self, machine_number, eeprom_uid, cartridge_crypted):
        cartridge_packed = self.decrypt(machine_number, eeprom_uid, cartridge_crypted)
        cartridge = self.unpack(cartridge_packed)
        return cartridge

    #
    # Pack a cartridge into a format suitable to be encrypted then burn
    # onto the cartridge EEPROM
    #
    def pack(self, cartridge):
        eeprom = bytearray(0x71)

        # serial number
        struct.pack_into("<d", eeprom, 0x0, cartridge.serial_number)
        # material id
        struct.pack_into("<d", eeprom, 0x08, material.get_id_from_name(cartridge.material_name))
        # manufacturing lot
        struct.pack_into("<20s", eeprom, 0x10, cartridge.manufacturing_lot.encode("utf-8"))
        # version (not sure)
        struct.pack_into("<H", eeprom, 0x24, cartridge.version)
        # manufacturing date
        mfg_dt = cartridge.manufacturing_date.ToDatetime()
        struct.pack_into("<HBBBBH", eeprom, 0x28,
                mfg_dt.year - 1900,
                mfg_dt.month,
                mfg_dt.day,
                mfg_dt.hour,
                mfg_dt.minute,
                mfg_dt.second)
        # last use date
        lu_dt = cartridge.last_use_date.ToDatetime()
        struct.pack_into("<HBBBBH", eeprom, 0x30,
                lu_dt.year - 1900,
                lu_dt.month,
                lu_dt.day,
                lu_dt.hour,
                lu_dt.minute,
                lu_dt.second)
        struct.pack_into("<d", eeprom, 0x38, cartridge.initial_material_quantity)
        # plaintext checksum
        struct.pack_into("<H", eeprom, 0x40, self.checksum.checksum(eeprom[0x00:0x40]))
        # key
        struct.pack_into("<8s", eeprom, 0x48, bytearray.fromhex(cartridge.key_fragment.decode()))
        # key checksum
        struct.pack_into("<H", eeprom, 0x50, self.checksum.checksum(eeprom[0x48:0x50]))
        # current material quantity
        struct.pack_into("<d", eeprom, 0x58, cartridge.current_material_quantity)
        # Checksum current material quantity
        struct.pack_into("<H", eeprom, 0x62, self.checksum.checksum(eeprom[0x58:0x60]))
        # signature (not sure, not usedu)
        struct.pack_into("<9s", eeprom, 0x68, cartridge.signature.encode("utf-8"))

        return eeprom

    #
    # Unpack a decrypted cartridge into a catridge object
    #
    def unpack(self, cartridge_packed):
        # Validating plaintext checksum
        if self.checksum.checksum(cartridge_packed[0x00:0x40]) != struct.unpack("<H", bytes(cartridge_packed[0x40:0x42]))[0]:
            raise Exception("invalid content checksum: should have " + hex(struct.unpack("<H", bytes(cartridge_packed[0x40:0x42]))[0]) + " but have " + hex(self.checksum.checksum(cartridge_packed[0x00:0x40])))

        # Validating current material quantity checksum
        if self.checksum.checksum(cartridge_packed[0x58:0x60]) != struct.unpack("<H", bytes(cartridge_packed[0x62:0x64]))[0]:
            raise Exception("invalid current material quantity checksum")

        cartridge_packed = memoryview(cartridge_packed)

        # Serial number
        serial_number = struct.unpack_from("<d", cartridge_packed, 0x0)[0]
        # Material
        material_name = material.get_name_from_id(int(struct.unpack_from("<d", cartridge_packed, 0x08)[0]))
        # Manufacturing lot
        manufacturing_lot = bytes(struct.unpack_from("<20s", cartridge_packed, 0x10)[0]).split(b'\x00')[0]
        # Manufacturing datetime
        (mfg_datetime_year,
            mfg_datetime_month,
            mfg_datetime_day,
            mfg_datetime_hour,
            mfg_datetime_minute,
            mfg_datetime_second) = struct.unpack_from("<HBBBBH", cartridge_packed, 0x28)
        mfg_datetime = datetime.datetime(mfg_datetime_year + 1900,
                mfg_datetime_month,
                mfg_datetime_day,
                mfg_datetime_hour,
                mfg_datetime_minute,
                mfg_datetime_second)
        # Last use datetime
        (use_datetime_year,
            use_datetime_month,
            use_datetime_day,
            use_datetime_hour,
            use_datetime_minute,
            use_datetime_second) = struct.unpack_from("<HBBBBH", cartridge_packed, 0x30)
        use_datetime = datetime.datetime(use_datetime_year + 1900,
                use_datetime_month,
                use_datetime_day,
                use_datetime_hour,
                use_datetime_minute,
                use_datetime_second)
        # Initial material quantity
        initial_material_quantity = struct.unpack_from("<d", cartridge_packed, 0x38)[0]
        # Version
        version = struct.unpack_from("<H", cartridge_packed, 0x24)[0]
        # Key fragment
        key_fragment = struct.unpack_from("<8s", cartridge_packed, 0x48)[0].hex()
        # Current material quantity
        current_material_quantity = struct.unpack_from("<d", cartridge_packed, 0x58)[0]
        # Signature
        signature = struct.unpack_from("<9s", cartridge_packed, 0x68)[0]

        c = cartridge_pb2.Cartridge()
        c.serial_number = serial_number
        c.material_name = material_name
        c.manufacturing_lot = manufacturing_lot
        c.manufacturing_date.FromDatetime(mfg_datetime)
        c.last_use_date.FromDatetime(use_datetime)
        c.initial_material_quantity = initial_material_quantity
        c.current_material_quantity = current_material_quantity
        c.key_fragment = key_fragment.encode("utf8")
        c.version = version
        c.signature = signature

        return c

    #
    # Encrypt a packed cartridge into a crypted cartridge
    #
    def encrypt(self, machine_number, eeprom_uid, cartridge_packed):
        cartridge_crypted = cartridge_packed

        # Validate key fragment checksum
        # TODO

        # Build the key
        key = self.build_key(cartridge_packed[0x48:0x50], machine_number, eeprom_uid)
        # Encrypt content
        struct.pack_into("<64s", cartridge_crypted, 0x00, bytes(self.crypto.encrypt(key, cartridge_packed[0x00:0x40])))
        # Checksum crypted content
        struct.pack_into("<H", cartridge_crypted, 0x46, self.checksum.checksum(cartridge_packed[0x00:0x40]))
        # Encrypt current material quantity
        struct.pack_into("<8s", cartridge_crypted, 0x58, bytes(self.crypto.encrypt(key, cartridge_packed[0x58:0x60])))
        # Checksum crypted current material quantity
        struct.pack_into("<H", cartridge_crypted, 0x60, self.checksum.checksum(cartridge_packed[0x58:0x60]))

        return cartridge_crypted

    #
    # Decrypt a crypted cartridge into a packed cartridge
    #
    def decrypt(self, machine_number, eeprom_uid, cartridge_crypted):
        cartridge_packed = cartridge_crypted

        # Validate key fragment checksum
        # TODO

        # Build the key
        key = self.build_key(cartridge_crypted[0x48:0x50], machine_number, eeprom_uid)
        # Validate crypted content checksum
        if self.checksum.checksum(cartridge_crypted[0x00:0x40]) != struct.unpack("<H", cartridge_crypted[0x46:0x48])[0]:
            raise Exception("invalid crypted content checksum")
        # Decrypt content
        cartridge_packed[0x00:0x40] = self.crypto.decrypt(key, cartridge_crypted[0x00:0x40])
        # Validate crypted current material quantity checksum
        if self.checksum.checksum(cartridge_crypted[0x58:0x60]) != struct.unpack("<H", bytes(cartridge_crypted[0x60:0x62]))[0]:
            raise Exception("invalid current material quantity checksum")
        # Decrypt current material quantity
        cartridge_packed[0x58:0x60] = self.crypto.decrypt(key, cartridge_crypted[0x58:0x60])

        return cartridge_packed

    #
    # Build a key used to encrypt/decrypt a cartridge
    #
    def build_key(self, cartridge_key, machine_number, eeprom_uid):
        machine_number = bytearray(machine_number)
        eeprom_uid = bytearray(eeprom_uid)
        key = bytearray(16)

        key[0] = ~cartridge_key[0] & 0xff
        key[1] = ~cartridge_key[2] & 0xff
        key[2] = ~eeprom_uid[2] & 0xff
        key[3] = ~cartridge_key[6] & 0xff
        key[4] = ~machine_number[0] & 0xff
        key[5] = ~machine_number[2] & 0xff
        key[6] = ~eeprom_uid[6] & 0xff
        key[7] = ~machine_number[6] & 0xff
        key[8] = ~machine_number[7] & 0xff
        key[9] = ~eeprom_uid[1] & 0xff
        key[10] = ~machine_number[3] & 0xff
        key[11] = ~machine_number[1] & 0xff
        key[12] = ~cartridge_key[7] & 0xff
        key[13] = ~eeprom_uid[5] & 0xff
        key[14] = ~cartridge_key[3] & 0xff
        key[15] = ~cartridge_key[1] & 0xff

        return key

