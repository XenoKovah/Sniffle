/*
 * Written by Sultan Qasim Khan
 * Copyright (c) 2016-2018, NCC Group plc
 * All rights reserved.
 */

/*********************************************************************
 * INCLUDES
 */
#include <errno.h>
#include <ti/sysbios/knl/Task.h>

// DriverLib
#include <ti/drivers/rf/RF.h>

#include "RFQueue.h"
#include "RadioWrapper.h"
#include "smartrf_settings.h"
#include "RadioTask.h"

/*********************************************************************
 * CONSTANTS
 */

/* TX Configuration: TODO: update and or correct this */
#define DATA_ENTRY_HEADER_SIZE 8    /* Constant header size of a Generic Data Entry */
#define MAX_LENGTH             255  /* Max length byte the radio will accept */
#define NUM_DATA_ENTRIES       2    /* NOTE: Only two data entries supported at the moment */
#define NUM_APPENDED_BYTES     2    /* The Data Entries data field will contain:
                                     * 1 Header byte (RF_cmdPropRx.rxConf.bIncludeHdr = 0x1)
                                     * Max 255 payload bytes
                                     * 1 status byte (RF_cmdPropRx.rxConf.bAppendStatus = 0x1) */

/*********************************************************************
 * LOCAL VARIABLES
 */

static RF_Object bleRfObject;
static RF_Handle bleRfHandle;

/* Receive dataQueue for RF Core to fill in data */
static dataQueue_t dataQueue;

/* Buffer which contains all Data Entries for receiving data.
 * Pragmas are needed to make sure this buffer is 4 byte aligned (requirement from the RF Core) */
static uint8_t rxDataEntryBuffer [RF_QUEUE_DATA_ENTRY_BUFFER_SIZE(NUM_DATA_ENTRIES,
            MAX_LENGTH, NUM_APPENDED_BYTES)] __attribute__ ((aligned (4)));

static bool configured = false;
static uint8_t last_channel = 0xFF;

rfc_bleGenericRxOutput_t recvStats;

static RadioWrapper_Callback userCallback = NULL;

static uint16_t * status_ptrs[3] = {NULL, NULL, NULL};

/*********************************************************************
 * LOCAL FUNCTIONS
 */
static void rx_int_callback(RF_Handle h, RF_CmdHandle ch, RF_EventMask e);

/*********************************************************************
 * PUBLIC FUNCTIONS
 */

int RadioWrapper_init()
{
    if(!configured)
    {
        bleRfHandle = RF_open(&bleRfObject, &RF_prop,
                        (RF_RadioSetup*)&RF_cmdBle5RadioSetup, NULL);

        if(bleRfHandle < 0)
        {
            return -ENODEV;
        }

		if( RFQueue_defineQueue(&dataQueue,
								rxDataEntryBuffer,
								sizeof(rxDataEntryBuffer),
								NUM_DATA_ENTRIES,
								MAX_LENGTH + NUM_APPENDED_BYTES))
		{
			/* Failed to allocate space for all data entries */
            return -ENOMEM;
		}

        configured = true;
    }

    return 0;
}

// Sniff/Receive BLE packets
//
// Arguments:
//  phy         PHY mode to use
//  chan        Channel to listen on
//  accessAddr  BLE access address of packet to listen for
//  crcInit     Initial CRC value of packets being listened for
//  timeout     When to stop listening (in radio ticks)
//  callback    Function to call when a packet is received
//
// Returns:
//  Status code (errno.h), 0 on success
int RadioWrapper_recvFrames(PHY_Mode phy, uint32_t chan, uint32_t accessAddr,
    uint32_t crcInit, uint32_t timeout, RadioWrapper_Callback callback)
{
    if((!configured) || (chan >= 40))
    {
        return -EINVAL;
    }

    userCallback = callback;

    /* set up the receive request */
	RF_cmdBle5GenericRx.pOutput = &recvStats;
    RF_cmdBle5GenericRx.channel = chan;
    RF_cmdBle5GenericRx.whitening.init = 0x40 + chan;
    RF_cmdBle5GenericRx.phyMode.mainMode = phy;
    RF_cmdBle5GenericRx.pParams->pRxQ = &dataQueue;
    RF_cmdBle5GenericRx.pParams->accessAddress = accessAddr;
    RF_cmdBle5GenericRx.pParams->crcInit0 = crcInit & 0xFF;
    RF_cmdBle5GenericRx.pParams->crcInit1 = (crcInit >> 8) & 0xFF;
    RF_cmdBle5GenericRx.pParams->crcInit2 = (crcInit >> 16) & 0xFF;
    RF_cmdBle5GenericRx.pParams->bRepeat = 0x01; // receive multiple packets

    RF_cmdBle5GenericRx.pParams->rxConfig.bAutoFlushIgnored = 1;
    RF_cmdBle5GenericRx.pParams->rxConfig.bAutoFlushCrcErr = 1;
    RF_cmdBle5GenericRx.pParams->rxConfig.bAutoFlushEmpty = 1;
    RF_cmdBle5GenericRx.pParams->rxConfig.bIncludeLenByte = 1;
    RF_cmdBle5GenericRx.pParams->rxConfig.bIncludeCrc = 0;
    RF_cmdBle5GenericRx.pParams->rxConfig.bAppendRssi = 0;
    RF_cmdBle5GenericRx.pParams->rxConfig.bAppendStatus = 0;
    RF_cmdBle5GenericRx.pParams->rxConfig.bAppendTimestamp = 0;

    /* receive forever if timeout == 0xFFFFFFFF */
    if (timeout != 0xFFFFFFFF)
    {
        // 4 MHz radio clock, so multiply microsecond timeout by 4
        RF_cmdBle5GenericRx.pParams->endTrigger.triggerType = TRIG_ABSTIME;
        RF_cmdBle5GenericRx.pParams->endTime = timeout;
    } else {
        RF_cmdBle5GenericRx.pParams->endTrigger.triggerType = TRIG_NEVER;
        RF_cmdBle5GenericRx.pParams->endTime = 0;
    }

    last_channel = chan;

	/* Enter RX mode and stay in RX till timeout */
    RF_runCmd(bleRfHandle, (RF_Op*)&RF_cmdBle5GenericRx, RF_PriorityNormal,
            &rx_int_callback, IRQ_RX_ENTRY_DONE);

    return 0;
}

#define CMD_LATENCY 150
int RadioWrapper_recvAdv3(uint32_t hopTicks, RadioWrapper_Callback callback)
{
    rfc_bleGenericRxPar_t para37;
    rfc_bleGenericRxPar_t para38;
    rfc_bleGenericRxPar_t para39;
    rfc_CMD_BLE5_GENERIC_RX_t sniff37;
    rfc_CMD_BLE5_GENERIC_RX_t sniff38;
    rfc_CMD_BLE5_GENERIC_RX_t sniff39;

    if (!configured)
        return -EINVAL;

    userCallback = callback;

    // commom parameters for sniffing advertisements
    para37.pRxQ = &dataQueue;
    para37.accessAddress = 0x8E89BED6;
    para37.crcInit0 = 0x55;
    para37.crcInit1 = 0x55;
    para37.crcInit2 = 0x55;
    para37.bRepeat = 0x01; // receive multiple packets
    para37.__dummy0 = 0x0000;
    para37.rxConfig.bAutoFlushIgnored = 1;
    para37.rxConfig.bAutoFlushCrcErr = 1;
    para37.rxConfig.bAutoFlushEmpty = 1;
    para37.rxConfig.bIncludeLenByte = 1;
    para37.rxConfig.bIncludeCrc = 0;
    para37.rxConfig.bAppendRssi = 0;
    para37.rxConfig.bAppendStatus = 0;
    para37.rxConfig.bAppendTimestamp = 0;
    para37.endTrigger.triggerType = TRIG_NEVER;
    para37.endTrigger.bEnaCmd = 0;
    para37.endTrigger.triggerNo = 0x0;
    para37.endTrigger.pastTrig = 1;
    para37.endTime = 0;

    // set up the first generic RX struct
    sniff37.commandNo = 0x1829;
    sniff37.status = 0x0000;
    sniff37.pNextOp = NULL;
    sniff37.startTime = 0x00000000;
    sniff37.startTrigger.triggerType = TRIG_NOW;
    sniff37.startTrigger.bEnaCmd = 0;
    sniff37.startTrigger.triggerNo = 0x0;
    sniff37.startTrigger.pastTrig = 1;
    sniff37.condition.rule = COND_ALWAYS;
    sniff37.condition.nSkip = 0x0;
    sniff37.channel = 0;
    sniff37.whitening.init = 0x00;
    sniff37.whitening.bOverride = 0;
    sniff37.phyMode.mainMode = PHY_1M;
    sniff37.phyMode.coding = 0x0;
    sniff37.rangeDelay = 0x00;
    sniff37.txPower = 0x0000;
    sniff37.pParams = NULL;
    sniff37.pOutput = &recvStats;
    sniff37.tx20Power = 0x00000000;

    // duplicate the default settings
    para38 = para37;
    para39 = para37;
    sniff38 = sniff37;
    sniff39 = sniff37;

    // sniff 37, wait for trigger, sniff 38, sniff 39
    sniff37.pNextOp = (RF_Op *)&sniff38;
    sniff37.pParams = &para37;
    sniff37.channel = 37;
    para37.endTrigger.triggerType = TRIG_NEVER;
    para37.endTrigger.bEnaCmd = 1;

    sniff38.pNextOp = (RF_Op *)&sniff39;
    sniff38.pParams = &para38;
    sniff38.channel = 38;
    para38.endTrigger.triggerType = TRIG_REL_PREVEND;
    para38.endTime = hopTicks - CMD_LATENCY;

    sniff39.pParams = &para39;
    sniff39.channel = 39;
    sniff39.condition.rule = COND_NEVER;
    para39.endTrigger.triggerType = TRIG_REL_PREVEND;
    para39.endTime = hopTicks;

    // special case to figure out which channel we're on
    last_channel = 40;
    status_ptrs[0] = &sniff37.status;
    status_ptrs[1] = &sniff38.status;
    status_ptrs[2] = &sniff39.status;

    // run the command chain
    RF_runCmd(bleRfHandle, (RF_Op*)&sniff37, RF_PriorityNormal,
            &rx_int_callback, IRQ_RX_ENTRY_DONE);

    return 0;
}

void RadioWrapper_trigAdv3()
{
    // trigger switch from chan 37 to 38
    RF_runDirectCmd(bleRfHandle, 0x04040001);
}

void RadioWrapper_stop()
{
    // Gracefully stop any radio operations
    RF_runDirectCmd(bleRfHandle, 0x04020001);
}

static void rx_int_callback(RF_Handle h, RF_CmdHandle ch, RF_EventMask e)
{
    BLE_Frame frame;
    rfc_dataEntryGeneral_t *currentDataEntry;
    uint8_t *packetPointer;
    uint8_t packetLength;
    uint8_t *packetDataPointer;

    if (e & RF_EventRxEntryDone)
    {
        /* Get current unhandled data entry */
        currentDataEntry = RFQueue_getDataEntry();
        packetPointer = (uint8_t *)(&currentDataEntry->data);


        /* In the current radio configuration:
         * Byte 0: Radio packet length
         * Byte 1: Start of actual packet
         *
         * For advertising packets
         * Bytes 1,2:   advertisement header
         * Bytes 3-8:   MAC address of sender (may be randomized)
         * Bytes 9-end: advertisement body
         */
        packetLength      = packetPointer[0];
        packetDataPointer = packetPointer + 1;

        /* Point the frame variable to the payload */
        frame.pData = packetDataPointer;

        /* 4 MHz clock, so divide by 4 to get microseconds */
        frame.timestamp = recvStats.timeStamp >> 2;
        frame.rssi = recvStats.lastRssi;
		frame.length = packetLength;

        if (last_channel < 40)
            frame.channel = last_channel;
        else if (*status_ptrs[0] <= ACTIVE)
            frame.channel = 37;
        else if (*status_ptrs[1] <= ACTIVE)
            frame.channel = 38;
        else
            frame.channel = 39;

        if (userCallback) userCallback(&frame);

        RFQueue_nextEntry();
    }
}

int RadioWrapper_close()
{
    if(!configured)
    {
        return -EINVAL;
    }

    RF_close(bleRfHandle);

    configured = false;

    return 0;
}
