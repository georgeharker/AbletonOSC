from ableton.v2.control_surface.component import Component
from typing import Optional, Tuple, Any
import logging
from .osc_server import OSCServer

class AbletonOSCHandler(Component):
    def __init__(self, manager):
        super().__init__()

        self.logger = logging.getLogger("abletonosc")
        self.manager = manager
        self.osc_server: OSCServer = self.manager.osc_server
        self.init_api()
        self.listener_functions = {}
        self.listener_objects = {}
        self.class_identifier = None

    def init_api(self):
        pass

    def clear_api(self):
        self._clear_listeners()

    #--------------------------------------------------------------------------------
    # Listener bookkeeping
    #--------------------------------------------------------------------------------
    def _listener_key(self, prop, params: Optional[Tuple] = (), client = None):
        """
        Key identifying one client's subscription to one property.

        Listeners are keyed per-client so that two clients may subscribe to the same
        property without evicting one another, and so each receives its own updates.
        `client` defaults to whoever sent the message being handled.
        """
        if client is None:
            client = self.osc_server.current_client
        return (prop, tuple(params), client)

    def stop_listeners_for_client(self, client) -> int:
        """
        Remove every listener registered by `client`. UDP gives us no disconnect
        signal, so a client that goes away silently would otherwise leave listeners
        firing into a dead port forever. Returns the number removed.
        """
        removed = 0
        for listener_key in list(self.listener_functions.keys()):
            if len(listener_key) == 3 and listener_key[2] == client:
                prop, params, key_client = listener_key
                target = self.listener_objects.get(listener_key)
                if target is None:
                    #--------------------------------------------------------------------------------
                    # Listeners registered outside _start_listen (device parameters, track
                    # mixer properties) record no target; drop the entry directly.
                    #--------------------------------------------------------------------------------
                    del self.listener_functions[listener_key]
                else:
                    self._stop_listen(target, prop, params, key_client)
                removed += 1
        return removed

    #--------------------------------------------------------------------------------
    # Generic callbacks
    #--------------------------------------------------------------------------------
    def _call_method(self, target, method, params: Optional[Tuple] = ()):
        self.logger.info("Calling method for %s: %s (params %s)" % (self.class_identifier, method, str(params)))
        getattr(target, method)(*params)

    def _set_property(self, target, prop, params: Tuple) -> None:
        self.logger.info("Setting property for %s: %s (new value %s)" % (self.class_identifier, prop, params[0]))
        setattr(target, prop, params[0])

    def _get_property(self, target, prop, params: Optional[Tuple] = ()) -> Tuple[Any]:
        try:
            value = getattr(target, prop)
        except RuntimeError:
            #--------------------------------------------------------------------------------
            # Gracefully handle errors, which may occur when querying parameters that don't apply
            # to a particular object (e.g. track.fold_state for a non-group track)
            #--------------------------------------------------------------------------------
            value = None
        self.logger.info("Getting property for %s: %s = %s" % (self.class_identifier, prop, value))
        return (value, *params)

    def _start_listen(self, target, prop, params: Optional[Tuple] = (), getter = None) -> None:
        """
        Start listening for the property named `prop` on the Live object `target`.
        `params` is typically a tuple containing the track/clip index.

        getter can be used for a customer getter when we're accessing native objects
        e.g. in view.py we don't return the selected_scene, but the selected_scene index.

        Args:
            target: 
            prop:
            params:
            getter:
        """
        #--------------------------------------------------------------------------------
        # Bind the subscribing client at registration time, so that changes are
        # delivered to whoever asked for them rather than to a single fixed port.
        #--------------------------------------------------------------------------------
        client = self.osc_server.current_client

        def property_changed_callback():
            if getter is None:
                value = getattr(target, prop)
            else:
                value = getter(params)
            if type(value) is not tuple:
                value = (value,)
            self.logger.info("Property %s changed of %s %s: %s" % (prop, self.class_identifier, str(params), value))
            osc_address = "/live/%s/get/%s" % (self.class_identifier, prop)
            self.osc_server.send(osc_address, (*params, *value,), remote_addr=client)

        listener_key = self._listener_key(prop, params, client)
        if listener_key in self.listener_functions:
            self._stop_listen(target, prop, params, client)

        self.logger.info("Adding listener for %s %s, property: %s" % (self.class_identifier, str(params), prop))
        add_listener_function_name = "add_%s_listener" % prop
        add_listener_function = getattr(target, add_listener_function_name)
        add_listener_function(property_changed_callback)
        self.listener_functions[listener_key] = property_changed_callback
        self.listener_objects[listener_key] = target
        #--------------------------------------------------------------------------------
        # Immediately send the current value
        #--------------------------------------------------------------------------------
        property_changed_callback()

    def _stop_listen(self, target, prop, params: Optional[Tuple[Any]] = (), client = None) -> None:
        listener_key = self._listener_key(prop, params, client)
        if listener_key in self.listener_functions:
            self.logger.info("Removing listener for %s %s, property %s" % (self.class_identifier, str(params), prop))
            listener_function = self.listener_functions[listener_key]
            remove_listener_function_name = "remove_%s_listener" % prop
            remove_listener_function = getattr(target, remove_listener_function_name)
            try:
                remove_listener_function(listener_function)
            except Exception as e:
                #--------------------------------------------------------------------------------
                # This exception may be thrown when an observer is no longer connected --
                # e.g., when trying to stop listening for a clip property of a clip that has been deleted.
                # Ignore as it is benign.
                #--------------------------------------------------------------------------------
                self.logger.info("Exception whilst removing listener (likely benign): %s" % e)

            del self.listener_functions[listener_key]
            del self.listener_objects[listener_key]
        else:
            self.logger.warning("No listener function found for property: %s (%s)" % (prop, str(params)))

    def _clear_listeners(self):
        """
        Clears all listener functions, to prevent listeners continuing to report after a reload.
        """
        for listener_key in list(self.listener_functions.keys())[:]:
            prop, params, client = listener_key
            target = self.listener_objects.get(listener_key)
            if target is None:
                #--------------------------------------------------------------------------------
                # Listeners registered outside _start_listen (device parameters, track
                # mixer properties) record no target object; drop the entry directly.
                #--------------------------------------------------------------------------------
                del self.listener_functions[listener_key]
            else:
                self._stop_listen(target, prop, params, client)
