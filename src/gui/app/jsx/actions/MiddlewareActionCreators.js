// Middleware Action Creators
// ==================================
// Handle high level Middleware events and actions, handle lifecycle and
// authentication changes, and call the dispatcher

"use strict";

var FreeNASDispatcher = require("../dispatcher/FreeNASDispatcher");
var FreeNASConstants  = require("../constants/FreeNASConstants");

var ActionTypes = FreeNASConstants.ActionTypes;

module.exports = {

    receiveAuthenticationChange: function ( authState ) {
      FreeNASDispatcher.handleMiddlewareAction({
          type      : ActionTypes.UPDATE_AUTH_STATE
        , authState : authState
      });
    }

  , increaseSubscriptionCount: function ( mask ) {
      FreeNASDispatcher.handleMiddlewareAction({
          type : ActionTypes.SUBSCRIBE_TO_MASK
        , mask : mask
      });
    }

  , decreaseSubscriptionCount: function ( mask ) {
      FreeNASDispatcher.handleMiddlewareAction({
          type : ActionTypes.UNSUBSCRIBE_FROM_MASK
        , mask : mask
      });
    }

  , receiveEventData: function ( eventData ) {
      FreeNASDispatcher.handleMiddlewareAction({
          type      : ActionTypes.MIDDLEWARE_EVENT
        , eventData : eventData
      });
    }

  , recieveAvailableServices: function ( services ) {
      FreeNASDispatcher.handleMiddlewareAction({
          type     : ActionTypes.RECIEVE_RPC_SERVICES
        , services : services
      });
    }

  , recieveAvailableServiceMethods: function ( service, methods ) {
      FreeNASDispatcher.handleMiddlewareAction({
          type    : ActionTypes.RECIEVE_RPC_SERVICE_METHODS
        , service : service
        , methods : methods
      });
    }

};
