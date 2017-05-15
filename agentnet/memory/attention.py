import theano.tensor as T
from agentnet.utils.layers import DictLayer
from lasagne.init import GlorotUniform

class AttentionLayer(DictLayer):
    """
    A layer that implements basic Bahdanau-style attention. Implementation is inspired by tfnn@yandex.

    Kurzgesagt, attention lets network decide which fraction of sequence/image should it view now
    by using small one-layer block that predicts (input_element,controller) -> do i want to see input_element
    for all input_elements. You can read more about it here - http://distill.pub/2016/augmented-rnns/ .

    This layer outputs a dict with keys "attn" and "probs"
    - attn - inputs processed with attention, shape [batch_size, enc_units]
    - probs - probabilities for each activation [batch_size, seq_length]

    This layer assumes input sequence/image/video/whatever to have 1 spatial dimension (see below).
    - rnn/emb format [batch,seq_len,units] works out of the box
    - 1d convolution format [batch,units,seq_len] needs dimshuffle(conv,[0,2,1])
    - 2d convolution format [batch,units,dim1,dim2] needs two-step procedure
      - step1 = dimshuffle(conv,[0,2,3,1])
      - step2 = reshape(step1,[-1,dim1*dim2,units])
    - higher dimensionality follows the same principle as 2d example above
    - reshape and dimshuffle can both be found in lasagne.layers (aliases to ReshapeLayer and DimshuffleLayer)

    When calling get_output, you can pass flag hard_attention=True to replace attention with argmax over logits.

    :param input_sequence: sequence of inputs to be processed with attention
    :type input_sequence: lasagne.layers.Layer with shape [batch,seq_length,units]

    :param conteroller_state: single time-step state of decoder (usually lstm/gru/rnn hid)
    :type controller_state: lasagne.layers.Layer with shape [batch,units]

    :param num_units: number of hidden units in attention intermediate activation
    :type num_units: int

    :param nonlinearity: nonlinearity in attention intermediate activation
    :type nonlinearity: function(x) -> x that works with theano tensors

    :param weights_nonlinearity: nonlinearity that converts logits of shape [batch,seq_length] into attention weights of same shape
        (you can provide softmax with tunable temperature or gumbel-softmax or anything of the sort)
    :type weights_nonlinearity: function(x) -> x that works with theano tensors


    :param mask_input: mask for input_sequence (like other lasagne masks). Default is no mask
    :type mask_input: lasagne.layers.Layer with shape [batch,seq_length]

    Other params can be theano shared variable, expression, numpy array or callable.
    Initial value, expression or initializer for the weights.
    These should be a matrix with shape ``(num_inputs, num_units)``.
    See :func:`lasagne.utils.create_param` for more information.

    The roles of those params are:
    W_enc - weights from encoder (each state) to hidden layer
    W_dec - weights from decoder (each state) to hidden layer
    W_out - hidden to logit weights
    No logit biases are introduces because softmax is invariant to adding bias to each logit

    """

    def __init__(self,
                 input_sequence,
                 controller_state,
                 num_units,
                 mask_input = None,
                 nonlinearity = T.tanh,
                 weights_nonlinearity=T.nnet.softmax,
                 W_enc = GlorotUniform(),
                 W_dec = GlorotUniform(),
                 W_out = GlorotUniform(),
            ):
        assert len(input_sequence.output_shape)==3,"input_sequence must be a 3-dimensional (batch,time,units)"
        assert len(controller_state.output_shape)==2,"controller_state must be a 2-dimensional for single tick (batch,units)"
        assert mask_input is None or len(mask_input.output_shape)==2,"mask_input must be 2-dimensional (batch,time) or None"

        batch_size,seq_len,enc_units = input_sequence.output_shape
        dec_units = controller_state.output_shape[-1]

        incomings = [input_sequence,controller_state]
        if mask_input is not None:
            incomings.append(mask_input)

        output_shapes = {'attn':(batch_size,enc_units),
                         'probs':(batch_size,seq_len)}

        super(AttentionLayer,self).__init__(incomings,output_shapes)



        self.W_enc = self.add_param(W_enc,(enc_units,num_units),name='enc_to_hid')
        self.W_dec = self.add_param(W_dec,(dec_units,num_units),name='dec_to_hid')
        self.W_out = self.add_param(W_out,(num_units,1),name='hid_to_logit')
        self.nonlinearity = nonlinearity
        self.weights_nonlinearity = weights_nonlinearity

    def get_output_for(self, inputs, hard_attention=False , **kwargs):
        """
        :param inputs: should consist of (enc_seq, dec) or  (enc_seq, dec, inp_mask)
        Shapes are
        enc_seq: [batch_size, seq_length, enc_units]
        dec: [batch_size, dec_units]
        inp_mask: [batch_size,seq_length] if any

        ---------------------------------
        :returns: dict with keys "attn" and "probs"
        - attn - inputs processed with attention, shape [batch_size, enc_size]
        - probs - probabilities for each activation [batch_size, ninp]
        """
        assert len(inputs) in (2,3),"inputs should be (enc_seq, dec) or  (enc_seq, dec, inp_mask)"
        mask_provided = len(inputs)==3

        #parse inputs
        enc_seq, dec = inputs[:2]
        if mask_provided:
            mask = inputs[-1]

        #Hidden layer activations, shape [batch,seq_len,hid_units]
        hid = self.nonlinearity(
            enc_seq.dot(self.W_enc) +\
            dec.dot(self.W_dec)[:,None,:]
        )


        #Logits from hidden. Mask implementation from tfnn

        logits = hid.dot(self.W_out)[:,:,0] # [batch_size,seq_len]

        if mask_provided:                  # substract large number from mask=0 time-steps
            logits -= (1 - mask) * 1000    # (written to match tfnn implementation)

        if not hard_attention:
            #regular soft attention, use softmax
            probs = self.weights_nonlinearity(logits)       # [batch_size,seq_len]

            # Compose attention.
            attn = T.sum(probs[:,:,None] * enc_seq, axis=1)

            return {'attn':attn, 'probs':probs}

        else: #hard_attention

            #use argmax over logits
            max_i = logits.argmax(axis=-1)
            batch_size = enc_seq.shape[0]
            attn = enc_seq[T.arange(batch_size),max_i]

            # one-hot probabilities
            one_hot = T.extra_ops.to_one_hot(max_i,logits.shape[1])

            return {'attn': attn, 'probs': one_hot }


